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
from dataclasses import dataclass
from typing import Optional

from app.xuanwu.core.config_schema import WebhookConfig, WebhookSystemConfig
from app.xuanwu.skills.registry import MdSkillEntry, SkillRegistry


_QUALIFIED_SKILL_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?:[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
)


@dataclass(frozen=True)
class WebhookSystemIdentity:
    """Resolved identity for an authenticated webhook caller."""

    system_id: str
    default_agent_id: str
    allowed_skills: tuple[str, ...]


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
            if not os.environ.get(system.sk_env, "").strip():
                raise RuntimeError(
                    f"Missing webhook secret in environment variable {system.sk_env!r}"
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
            expected = os.environ.get(system.sk_env, "").strip()
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


def build_webhook_user_message(skill_entry: MdSkillEntry, payload: dict, system_id: str) -> str:
    """Build a deterministic prompt that targets a single markdown skill."""
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
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
