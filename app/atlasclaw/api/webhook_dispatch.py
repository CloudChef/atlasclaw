# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""Webhook markdown-skill dispatch helpers."""

from __future__ import annotations

import hmac
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

from app.atlasclaw.api.service_provider_schemas import (
    SUPPORTED_PROVIDER_AUTH_TYPES,
    get_provider_schema_definition,
)
from app.atlasclaw.core.config_schema import WebhookConfig, WebhookSystemConfig
from app.atlasclaw.core.trace import sanitize_log_value
from app.atlasclaw.skills.registry import MdSkillEntry, SkillRegistry


_QUALIFIED_SKILL_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?:[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
)
_ROBOT_AUTH_KEY = "robot_auth"
_ROBOT_PROFILE_KEY = "robot_profile"
_PROVIDER_INSTANCE_KEY = "provider_instance"
_PROFILE_METADATA_KEYS = frozenset(
    {
        "allowed_skills",
        "description",
        "display_name",
        "name",
        "profile",
        "profile_id",
        "title",
    }
)


class WebhookRobotProfileError(ValueError):
    """Expected webhook robot profile validation failure."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class WebhookSystemIdentity:
    """Resolved identity for an authenticated webhook caller."""

    system_id: str
    default_agent_id: str
    allowed_skills: tuple[str, ...]


@dataclass(frozen=True)
class WebhookRobotProfileSelection:
    """Runtime-only provider selection derived from webhook robot profile args.

    The provider_config here is dispatch-scoped. It must contain only the
    selected provider instance and robot credentials for this single webhook run.
    """

    provider_type: str
    provider_instance: str
    robot_profile: str
    provider_config: dict[str, dict[str, dict[str, Any]]]
    provider_instance_config: dict[str, Any]
    args: dict[str, Any]


class WebhookDispatchManager:
    """Authenticate webhook calls and resolve provider-qualified markdown skills."""

    def __init__(self, config: WebhookConfig, skill_registry: SkillRegistry) -> None:
        self._config = config
        self._skill_registry = skill_registry

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def header_name(self) -> str:
        return self._config.header_name

    def validate_startup(self) -> None:
        """Fail fast when webhook config references missing env vars or skills."""
        if not self.enabled:
            return
        if not self._config.systems:
            raise RuntimeError("webhook.systems must not be empty when webhook.enabled=true")

        seen_qualified: set[str] = set()
        for entry in self._skill_registry.md_snapshot():
            qualified_name = str(entry.get("qualified_name", "")).strip()
            if not qualified_name:
                continue
            if qualified_name in seen_qualified:
                raise RuntimeError(f"Duplicate webhook markdown skill: {qualified_name}")
            seen_qualified.add(qualified_name)

        executable_names = set(self._skill_registry.list_skills())
        for system in self._config.systems:
            if not system.enabled:
                continue
            if not _resolve_webhook_system_secret(system):
                raise RuntimeError(
                    f"Missing webhook secret for system {system.system_id!r} from sk_env"
                )
            for skill_id in system.allowed_skills:
                self._validate_skill_identifier(skill_id)
                if skill_id in executable_names:
                    raise RuntimeError(
                        f"Webhook skill {skill_id!r} resolves to an executable tool; only markdown skills are allowed"
                    )
                skill_entry = self._skill_registry.get_md_skill(skill_id)
                if skill_entry is None or skill_entry.qualified_name != skill_id:
                    raise RuntimeError(
                        f"Webhook allowed skill {skill_id!r} not found as a unique markdown skill"
                    )

    def authenticate(self, secret: str) -> Optional[WebhookSystemIdentity]:
        """Resolve the calling system from the shared secret."""
        candidate = (secret or "").strip()
        if not candidate:
            return None

        for system in self._config.systems:
            if not system.enabled:
                continue
            expected = _resolve_webhook_system_secret(system)
            if expected and hmac.compare_digest(expected, candidate):
                return WebhookSystemIdentity(
                    system_id=system.system_id,
                    default_agent_id=system.default_agent_id,
                    allowed_skills=tuple(system.allowed_skills),
                )
        return None

    def resolve_allowed_skill(
        self,
        identity: WebhookSystemIdentity,
        skill_id: str,
    ) -> Optional[MdSkillEntry]:
        """Resolve a provider-qualified markdown skill that the system may invoke."""
        normalized = (skill_id or "").strip()
        self._validate_skill_identifier(normalized)
        if normalized not in identity.allowed_skills:
            return None

        skill_entry = self._skill_registry.get_md_skill(normalized)
        if skill_entry is None or skill_entry.qualified_name != normalized:
            return None
        return skill_entry

    @staticmethod
    def _validate_skill_identifier(skill_id: str) -> None:
        if not _QUALIFIED_SKILL_RE.match(skill_id):
            raise RuntimeError(
                f"Invalid webhook skill identifier {skill_id!r}; expected provider:skill"
        )


def _resolve_webhook_system_secret(system: WebhookSystemConfig) -> str:
    """Resolve sk_env as an env var name first, then as a direct secret."""
    configured = (system.sk_env or "").strip()
    if not configured:
        return ""

    env_value = os.environ.get(configured)
    if env_value is not None:
        return env_value.strip()
    return configured


def redact_webhook_payload(payload: dict[str, Any], *, provider_type: str = "") -> dict[str, Any]:
    """Redact webhook payload data before it is copied into prompts, traces, or extras."""
    redacted = sanitize_log_value(
        payload or {},
        redacted_text="[REDACTED]",
        provider_type=provider_type,
        field_defaults=payload or {},
    )
    return redacted if isinstance(redacted, dict) else {}


def resolve_webhook_robot_profile_selection(
    *,
    skill_entry: MdSkillEntry,
    args: dict[str, Any],
    service_provider_registry: Any,
) -> Optional[WebhookRobotProfileSelection]:
    """Resolve webhook robot profile args to a narrowed runtime-only provider config."""
    webhook_args = dict(args or {})
    robot_profile = str(webhook_args.get(_ROBOT_PROFILE_KEY, "") or "").strip()
    if not robot_profile:
        return None

    provider_type = str(skill_entry.provider or "").strip().lower()
    if not provider_type:
        raise WebhookRobotProfileError(
            f"Webhook skill {skill_entry.qualified_name!r} is not provider-qualified",
            status_code=400,
        )

    provider_instance = str(webhook_args.get(_PROVIDER_INSTANCE_KEY, "") or "").strip()
    if not provider_instance:
        raise WebhookRobotProfileError(
            "webhook args.provider_instance is required when robot_profile is set",
            status_code=400,
        )

    if service_provider_registry is None:
        raise WebhookRobotProfileError(
            f"Provider instance not found: {provider_type}.{provider_instance}",
            status_code=400,
        )

    instance_config = service_provider_registry.get_instance_config(
        provider_type,
        provider_instance,
    )
    if not isinstance(instance_config, dict):
        raise WebhookRobotProfileError(
            f"Provider instance not found: {provider_type}.{provider_instance}",
            status_code=400,
        )

    profile_config = _get_robot_profile_config(instance_config, robot_profile)
    if profile_config is None:
        raise WebhookRobotProfileError(
            f"Robot profile not found: {provider_type}.{provider_instance}.{robot_profile}",
            status_code=400,
        )

    allowed_skills = _parse_allowed_skill_ids(robot_profile, profile_config.get("allowed_skills"))
    if skill_entry.qualified_name not in allowed_skills:
        raise WebhookRobotProfileError(
            (
                f"Robot profile {robot_profile!r} is not allowed to invoke "
                f"webhook skill {skill_entry.qualified_name!r}"
            ),
            status_code=403,
        )

    # Only top-level fields on robot_auth.<profile> are treated as credentials.
    # Nested auth/config containers are rejected so there is exactly one profile shape.
    profile_auth_fields = _extract_robot_profile_auth_fields(profile_config)
    selected_auth_type = _validate_robot_profile_auth_fields(
        provider_type=provider_type,
        robot_profile=robot_profile,
        profile_auth_fields=profile_auth_fields,
    )
    runtime_instance_config = _build_robot_profile_runtime_instance_config(
        provider_type=provider_type,
        instance_name=provider_instance,
        instance_config=instance_config,
        profile_auth_fields=profile_auth_fields,
        selected_auth_type=selected_auth_type,
    )
    return WebhookRobotProfileSelection(
        provider_type=provider_type,
        provider_instance=provider_instance,
        robot_profile=robot_profile,
        provider_config={
            provider_type: {
                provider_instance: runtime_instance_config,
            }
        },
        provider_instance_config=runtime_instance_config,
        args=webhook_args,
    )


def build_webhook_user_message(
    skill_entry: MdSkillEntry,
    payload: dict[str, Any],
    system_id: str,
) -> str:
    """Build a deterministic prompt that targets a single markdown skill."""
    safe_payload = redact_webhook_payload(payload, provider_type=skill_entry.provider)
    payload_json = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True)
    return (
        "You are handling a backend webhook task.\n"
        f"Target markdown skill: {skill_entry.qualified_name}\n"
        f"Skill file path: {skill_entry.file_path}\n"
        f"Calling system: {system_id}\n"
        "You must follow only the targeted markdown skill and prefer any executable tool already registered for it.\n"
        "Do not choose a different skill.\n"
        "Treat the JSON below as the complete machine-provided business input.\n"
        "Return a single structured JSON result.\n\n"
        f"{payload_json}"
    )


def _get_robot_profile_config(
    instance_config: dict[str, Any],
    robot_profile: str,
) -> Optional[dict[str, Any]]:
    robot_auth = instance_config.get(_ROBOT_AUTH_KEY)
    if not isinstance(robot_auth, dict):
        return None

    candidate = robot_auth.get(robot_profile)
    if isinstance(candidate, dict):
        return dict(candidate)
    return None


def _parse_allowed_skill_ids(robot_profile: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise WebhookRobotProfileError(
            f"Robot profile {robot_profile!r} must define allowed_skills as a non-empty list",
            status_code=400,
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise WebhookRobotProfileError(
                f"Robot profile {robot_profile!r} has invalid allowed_skills entry",
                status_code=400,
            )
        skill_id = item.strip()
        if skill_id in seen:
            continue
        normalized.append(skill_id)
        seen.add(skill_id)
    return tuple(normalized)


def _build_robot_profile_runtime_instance_config(
    *,
    provider_type: str,
    instance_name: str,
    instance_config: dict[str, Any],
    profile_auth_fields: dict[str, Any],
    selected_auth_type: str,
) -> dict[str, Any]:
    definition = _provider_schema_definition_or_error(provider_type)
    provider_connection_config = dict(instance_config)
    provider_connection_config.pop(_ROBOT_AUTH_KEY, None)
    # Keep provider connection fields, strip inactive auth fields, then overlay
    # the robot credential. This prevents normal user/cookie credentials from
    # riding along with a robot execution.
    runtime_config = definition.strip_auth_fields_for_runtime(
        provider_connection_config,
        selected_auth_type,
    )
    runtime_config["provider_type"] = provider_type
    runtime_config["instance_name"] = instance_name
    runtime_config.update(profile_auth_fields)
    runtime_config["auth_type"] = selected_auth_type
    runtime_config.pop(_ROBOT_AUTH_KEY, None)
    return runtime_config


def _provider_schema_definition_or_error(provider_type: str) -> Any:
    definition = get_provider_schema_definition(provider_type)
    if definition is None:
        raise WebhookRobotProfileError(
            f"Provider schema not found for {provider_type!r}; robot profiles require a provider schema",
            status_code=400,
        )
    return definition


def _extract_robot_profile_auth_fields(profile_config: dict[str, Any]) -> dict[str, Any]:
    auth_config: dict[str, Any] = {}
    for key, value in profile_config.items():
        key_str = str(key or "").strip()
        if not key_str or key_str in _PROFILE_METADATA_KEYS:
            continue
        auth_config[key_str] = deepcopy(value)
    auth_config.pop(_ROBOT_AUTH_KEY, None)
    return auth_config


def _validate_robot_profile_auth_fields(
    *,
    provider_type: str,
    robot_profile: str,
    profile_auth_fields: dict[str, Any],
) -> str:
    auth_type = _parse_robot_profile_auth_type(
        robot_profile,
        profile_auth_fields.get("auth_type"),
    )
    definition = _provider_schema_definition_or_error(provider_type)
    schema_required_fields = definition.required_fields_for_auth_type(auth_type)
    required_groups = (schema_required_fields,) if schema_required_fields else ()

    if required_groups and not _any_required_field_group_present(profile_auth_fields, required_groups):
        missing = " or ".join(
            "+".join(field_name for field_name in group)
            for group in required_groups
        )
        raise WebhookRobotProfileError(
            f"Robot profile {robot_profile!r} is missing required auth fields for {auth_type}: {missing}",
            status_code=400,
        )
    return auth_type


def _parse_robot_profile_auth_type(robot_profile: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebhookRobotProfileError(
            f"Robot profile {robot_profile!r} must define a single auth_type string",
            status_code=400,
        )
    auth_type = value.strip().lower()
    if auth_type not in SUPPORTED_PROVIDER_AUTH_TYPES:
        raise WebhookRobotProfileError(
            f"Robot profile {robot_profile!r} has invalid auth_type: Unsupported auth_type: {auth_type}",
            status_code=400,
        )
    return auth_type


def _has_non_blank_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return bool(value) and any(_has_non_blank_value(item) for item in value)
    return True


def _any_required_field_group_present(
    profile_auth_fields: dict[str, Any],
    required_groups: tuple[tuple[str, ...], ...],
) -> bool:
    for group in required_groups:
        if all(_has_non_blank_value(profile_auth_fields.get(field_name)) for field_name in group):
            return True
    return False
