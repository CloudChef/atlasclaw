# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Validated v1 embed route manifests and context snapshots."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictEmbedModel(BaseModel):
    """Base model that rejects undeclared v1 contract fields."""

    model_config = ConfigDict(extra="forbid")


def normalize_absolute_host_path(path: str) -> str:
    """Return one safe normalized absolute path without URL or traversal parts."""
    value = str(path or "").strip()
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "//" in value
        or any(token in value for token in ("?", "#", "\\", "://"))
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("path must be a normalized absolute path without URL parts")
    for segment in value.split("/")[1:]:
        if re.search(r"%(?![0-9A-Fa-f]{2})", segment):
            raise ValueError("path contains an unsafe encoded or traversal segment")
        decoded = unquote(segment)
        if (
            decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or re.search(r"%[0-9A-Fa-f]{2}", decoded)
            or any(ord(character) < 32 for character in decoded)
        ):
            raise ValueError("path contains an unsafe encoded or traversal segment")
    return value if value == "/" else value.rstrip("/")


class RouteMatch(StrictEmbedModel):
    """A deterministic normalized-path template matcher."""

    path_template: str = Field(min_length=1, max_length=512)

    @field_validator("path_template")
    @classmethod
    def validate_path_template(cls, value: str) -> str:
        """Require a safe normalized absolute template at the provider boundary."""
        return normalize_absolute_host_path(value)


class ResolverBinding(StrictEmbedModel):
    """Provider-owned entrypoint shared by every route in one manifest."""

    entrypoint: str = Field(min_length=1, max_length=512)


class RouteResult(StrictEmbedModel):
    """Context and existing Skill selected by a route rule."""

    page_type: str = Field(min_length=1, max_length=128)
    object_type: str = Field(min_length=1, max_length=128)
    skill_ref: str = Field(min_length=1, max_length=128)


class RouteRule(StrictEmbedModel):
    """One deterministic path-to-context route rule."""

    id: str = Field(min_length=1, max_length=128)
    priority: int = 100
    match: RouteMatch
    result: RouteResult


class RouteManifest(StrictEmbedModel):
    """Provider-owned route manifest with a frozen schema version."""

    schema_version: Literal[1]
    provider_type: str = Field(min_length=1, max_length=128)
    context_resolver: ResolverBinding
    routes: list[RouteRule] = Field(default_factory=list, max_length=256)


class ResolvedObject(StrictEmbedModel):
    """Small provider-neutral object projection returned by a resolver."""

    type: str = Field(min_length=1, max_length=128)
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=512)
    state: str = Field(default="", max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Bound Provider metadata retained in memory and injected into prompts."""
        if len(value) > 64:
            raise ValueError("object attributes exceed the field limit")
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("object attributes must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > 32 * 1024:
            raise ValueError("object attributes exceed the size limit")
        return value


class ContextSkillTool(StrictEmbedModel):
    """Presentation metadata for one Tool owned by the matched page Skill."""

    name: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)


class ContextSnapshot(StrictEmbedModel):
    """User-bound immutable page context retained only in process memory."""

    context_id: str
    owner_user_id: str
    surface_id: str = Field(
        min_length=22,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    generation: int = Field(ge=0)
    provider_type: str
    provider_instance: str
    page_type: str
    skill_ref: str
    object: ResolvedObject
    object_actions: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    tools: list[ContextSkillTool] = Field(default_factory=list, max_length=128)
    created_at: datetime
    expires_at: datetime


class MatchedRoute(StrictEmbedModel):
    """A route rule paired with its decoded single-segment parameters."""

    rule: RouteRule
    parameters: dict[str, str] = Field(default_factory=dict)
