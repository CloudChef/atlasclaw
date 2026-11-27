# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.

"""Persistence operations for global Provider and Skill model policies."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db.models import CapabilityModelPolicyModel

logger = logging.getLogger(__name__)


class RevisionConflictError(RuntimeError):
    """Raised when a policy update was based on an obsolete revision."""


class ModelPolicyValidationError(ValueError):
    """Raised when a policy does not describe a valid explicit or inherited range."""


def target_identity(target: Any) -> tuple[str, str, str, str]:
    """Return the normalized database identity for a binding target."""
    if isinstance(target, dict):
        get_value = target.get
    else:
        get_value = lambda name, default="": getattr(target, name, default)

    kind = str(get_value("kind", get_value("target_kind", "")) or "").strip()
    provider_type = str(get_value("provider_type", "") or "").strip()
    instance_name = str(get_value("instance_name", "") or "").strip()
    skill_id = str(get_value("skill_id", "") or "").strip()

    if kind == "provider":
        if not provider_type or not instance_name or skill_id:
            raise ValueError("Provider targets require provider_type and instance_name only")
    elif kind == "provider_skill":
        if not provider_type or not instance_name or not skill_id:
            raise ValueError("Provider Skill targets require provider_type, instance_name, and skill_id")
    elif kind == "standalone_skill":
        if not skill_id or provider_type or instance_name:
            raise ValueError("Standalone Skill targets require skill_id only")
    else:
        raise ValueError("Unknown model binding target kind")

    return kind, provider_type, instance_name, skill_id


def normalize_model_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the canonical writable three-field policy shape."""
    if not isinstance(policy, dict):
        raise ModelPolicyValidationError("model_policy must be an object")
    allowed_keys = {"mode", "allowed_model_refs", "default_model_ref"}
    if set(policy) - allowed_keys:
        raise ModelPolicyValidationError("model_policy contains unsupported fields")
    mode = str(policy.get("mode") or "").strip()
    refs = policy.get("allowed_model_refs")
    default_ref = policy.get("default_model_ref")
    if mode not in {"inherit", "explicit"}:
        raise ModelPolicyValidationError("model_policy.mode must be inherit or explicit")
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ModelPolicyValidationError("allowed_model_refs must be a list of model references")
    normalized_refs = [ref.strip() for ref in refs]
    if any(not ref for ref in normalized_refs):
        raise ModelPolicyValidationError("allowed_model_refs cannot contain empty references")
    if len(normalized_refs) != len(set(normalized_refs)):
        raise ModelPolicyValidationError("allowed_model_refs cannot contain duplicates")
    if default_ref is not None:
        if not isinstance(default_ref, str) or not default_ref.strip():
            raise ModelPolicyValidationError("default_model_ref must be a model reference or null")
        default_ref = default_ref.strip()
    if mode == "inherit" and (normalized_refs or default_ref is not None):
        raise ModelPolicyValidationError("Inherited policies must not contain model references")
    if mode == "explicit":
        if not normalized_refs:
            raise ModelPolicyValidationError("Explicit policies require at least one allowed model")
        if default_ref not in normalized_refs:
            raise ModelPolicyValidationError("The default model must be in allowed_model_refs")
    return {
        "mode": mode,
        "allowed_model_refs": normalized_refs,
        "default_model_ref": default_ref,
    }


class CapabilityModelPolicyService:
    """Read and atomically update revisioned global model policy records."""

    @staticmethod
    async def get_by_target(
        session: AsyncSession,
        target: Any,
    ) -> CapabilityModelPolicyModel | None:
        """Fetch a policy by its canonical target identity."""
        kind, provider_type, instance_name, skill_id = target_identity(target)
        result = await session.execute(
            select(CapabilityModelPolicyModel).where(
                CapabilityModelPolicyModel.target_kind == kind,
                CapabilityModelPolicyModel.provider_type == provider_type,
                CapabilityModelPolicyModel.instance_name == instance_name,
                CapabilityModelPolicyModel.skill_id == skill_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> list[CapabilityModelPolicyModel]:
        """Return all committed policy records in deterministic target order."""
        result = await session.execute(
            select(CapabilityModelPolicyModel).order_by(
                CapabilityModelPolicyModel.target_kind.asc(),
                CapabilityModelPolicyModel.provider_type.asc(),
                CapabilityModelPolicyModel.instance_name.asc(),
                CapabilityModelPolicyModel.skill_id.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def save_many(
        session: AsyncSession,
        records: list[dict[str, Any]],
        *,
        updated_by: str | None,
    ) -> list[CapabilityModelPolicyModel]:
        """Save a policy aggregate atomically using per-target optimistic revisions.

        A new target has revision 0 until its first write. Successful writes use
        revision 1 and increment existing rows exactly once.
        """
        prepared: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for record in records:
            if not isinstance(record, dict):
                raise ModelPolicyValidationError("Each policy record must be an object")
            identity = target_identity(record.get("target"))
            if identity in seen:
                raise ModelPolicyValidationError("A target may only appear once in an update")
            seen.add(identity)
            try:
                expected_revision = int(record.get("revision"))
            except (TypeError, ValueError) as exc:
                raise ModelPolicyValidationError("revision must be a non-negative integer") from exc
            if expected_revision < 0:
                raise ModelPolicyValidationError("revision must be a non-negative integer")
            policy = normalize_model_policy(record.get("model_policy"))
            prepared.append({
                "identity": identity,
                "expected_revision": expected_revision,
                "enabled": record.get("enabled"),
                "enabled_provided": "enabled" in record,
                "model_mode": policy["mode"],
                "allowed_model_refs": policy["allowed_model_refs"],
                "default_model_ref": policy["default_model_ref"],
            })

        if not prepared:
            return []

        current_rows: dict[tuple[str, str, str, str], CapabilityModelPolicyModel] = {}
        for item in prepared:
            kind, provider_type, instance_name, skill_id = item["identity"]
            result = await session.execute(
                select(CapabilityModelPolicyModel).where(
                    CapabilityModelPolicyModel.target_kind == kind,
                    CapabilityModelPolicyModel.provider_type == provider_type,
                    CapabilityModelPolicyModel.instance_name == instance_name,
                    CapabilityModelPolicyModel.skill_id == skill_id,
                )
            )
            row = result.scalar_one_or_none()
            current_revision = row.revision if row is not None else 0
            if current_revision != item["expected_revision"]:
                raise RevisionConflictError("The model policy changed; refresh before saving")
            if row is not None:
                current_rows[item["identity"]] = row

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            async with session.begin_nested():
                for item in prepared:
                    identity = item["identity"]
                    current = current_rows.get(identity)
                    expected = item["expected_revision"]
                    enabled = item["enabled"]
                    if current is None:
                        session.add(CapabilityModelPolicyModel(
                            target_kind=identity[0],
                            provider_type=identity[1],
                            instance_name=identity[2],
                            skill_id=identity[3],
                            enabled=enabled,
                            model_mode=item["model_mode"],
                            allowed_model_refs=item["allowed_model_refs"],
                            default_model_ref=item["default_model_ref"],
                            revision=1,
                            updated_by=updated_by,
                            updated_at=now,
                        ))
                        continue

                    value_map: dict[str, Any] = {
                        "model_mode": item["model_mode"],
                        "allowed_model_refs": item["allowed_model_refs"],
                        "default_model_ref": item["default_model_ref"],
                        "revision": expected + 1,
                        "updated_by": updated_by,
                        "updated_at": now,
                    }
                    if item["enabled_provided"]:
                        value_map["enabled"] = enabled
                    result = await session.execute(
                        update(CapabilityModelPolicyModel)
                        .where(
                            CapabilityModelPolicyModel.id == current.id,
                            CapabilityModelPolicyModel.revision == expected,
                        )
                        .values(**value_map)
                        .execution_options(synchronize_session=False)
                    )
                    if result.rowcount != 1:
                        raise RevisionConflictError("The model policy changed; refresh before saving")
                    await session.refresh(current)

                await session.flush()
        except IntegrityError as exc:
            raise RevisionConflictError("The model policy changed; refresh before saving") from exc

        saved_rows: list[CapabilityModelPolicyModel] = []
        for item in prepared:
            kind, provider_type, instance_name, skill_id = item["identity"]
            row = await CapabilityModelPolicyService.get_by_target(
                session,
                {
                    "kind": kind,
                    "provider_type": provider_type,
                    "instance_name": instance_name,
                    "skill_id": skill_id,
                },
            )
            if row is None:
                raise RuntimeError("Saved model policy could not be reloaded")
            saved_rows.append(row)
            logger.info(
                "Saved model policy target=%s provider_type=%s instance_name=%s skill_id=%s revision=%s",
                kind,
                provider_type,
                instance_name,
                skill_id,
                row.revision,
            )
        return saved_rows
