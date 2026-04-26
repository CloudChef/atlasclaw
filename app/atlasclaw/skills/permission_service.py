# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Skill permission policy service.

This service owns the boundary between role-facing skill configuration and the
runtime registry. Runtime code still works with concrete tool/skill IDs, while
role management presents standalone markdown skills plus built-in tool groups.
Provider-bound tools and skills are governed by provider permissions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.atlasclaw.tools.catalog import GROUP_ATLASCLAW, GROUP_TOOLS


class SkillPermissionService:
    """Central service for skill permission catalog and runtime filtering."""

    role_tool_group_ids: tuple[str, ...] = tuple(
        group_id
        for group_id, tool_names in GROUP_TOOLS.items()
        if group_id != GROUP_ATLASCLAW and tool_names
    )

    def normalize_id(self, value: Any) -> str:
        """Return a trimmed display/runtime identifier for permission comparisons."""
        return str(value or "").strip()

    def normalize_key(self, value: Any) -> str:
        """Return a case-insensitive normalized key for permission matching."""
        return self.normalize_id(value).lower()

    def canonical_tool_group_id(self, value: Any) -> str:
        """Return the canonical built-in tool group ID matching the input value."""
        key = self.normalize_key(value)
        if not key:
            return ""
        for group_id in GROUP_TOOLS:
            if self.normalize_key(group_id) == key:
                return group_id
        return ""

    def skill_identifier_matches(self, candidate: Any, target: Any) -> bool:
        """Return True when two skill IDs match directly or by unqualified suffix."""
        candidate_key = self.normalize_key(candidate)
        target_key = self.normalize_key(target)
        if not candidate_key or not target_key:
            return False
        return candidate_key == target_key or candidate_key.split(":")[-1] == target_key.split(":")[-1]

    def provider_type_from_tool_snapshot(self, tool: dict[str, Any]) -> str:
        """Extract the provider type from an executable tool snapshot."""
        provider_type = self.normalize_key(tool.get("provider_type"))
        if provider_type:
            return provider_type

        capability_class = self.normalize_key(tool.get("capability_class"))
        if capability_class.startswith("provider:"):
            inferred_provider = capability_class.split(":", 1)[1].strip()
            if inferred_provider and inferred_provider != "generic":
                return inferred_provider
        return ""

    def provider_type_from_md_skill_snapshot(self, skill: dict[str, Any]) -> str:
        """Extract the provider type from a markdown skill snapshot."""
        metadata = skill.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        return self.normalize_key(
            metadata.get("provider_type")
            or skill.get("provider_type")
            or skill.get("provider")
        )

    def is_provider_bound_tool_snapshot(self, tool: dict[str, Any]) -> bool:
        """Return True when an executable tool is governed by provider permissions."""
        if not isinstance(tool, dict):
            return False
        if self.provider_type_from_tool_snapshot(tool):
            return True
        return self.normalize_key(tool.get("source")) == "provider"

    def is_provider_bound_md_skill_snapshot(self, skill: dict[str, Any]) -> bool:
        """Return True when a markdown skill is governed by provider permissions."""
        if not isinstance(skill, dict):
            return False
        return bool(self.provider_type_from_md_skill_snapshot(skill))

    def is_markdown_backed_tool_snapshot(self, tool: dict[str, Any]) -> bool:
        """Return True when a tool was registered from a standalone markdown skill."""
        if not isinstance(tool, dict):
            return False
        return self.normalize_key(tool.get("source")) == "md_skill"

    def is_core_catalog_tool_snapshot(self, tool: dict[str, Any]) -> bool:
        """Return True when an executable tool should appear directly in core catalog."""
        return (
            isinstance(tool, dict)
            and not self.is_provider_bound_tool_snapshot(tool)
            and not self.is_markdown_backed_tool_snapshot(tool)
        )

    def visible_provider_types(
        self,
        provider_instances: dict[str, dict[str, dict[str, Any]]],
    ) -> set[str]:
        """Return provider types that still have at least one visible instance."""
        return {
            self.normalize_key(provider_type)
            for provider_type, instances in (provider_instances or {}).items()
            if self.normalize_key(provider_type) and isinstance(instances, dict) and instances
        }

    def filter_provider_bound_snapshots(
        self,
        tools_snapshot: list[dict[str, Any]],
        md_skills_snapshot: list[dict[str, Any]],
        provider_instances: dict[str, dict[str, dict[str, Any]]],
        *,
        enforce: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Hide provider-bound tools/skills when no visible instance remains."""
        visible_providers = self.visible_provider_types(provider_instances)
        if not visible_providers and not enforce:
            return tools_snapshot, md_skills_snapshot
        if not tools_snapshot and not md_skills_snapshot:
            return tools_snapshot, md_skills_snapshot

        filtered_tools: list[dict[str, Any]] = []
        for tool in tools_snapshot or []:
            if not isinstance(tool, dict):
                continue
            provider_type = self.provider_type_from_tool_snapshot(tool)
            if provider_type and provider_type not in visible_providers:
                continue
            if not provider_type and self.is_provider_bound_tool_snapshot(tool) and enforce:
                continue
            filtered_tools.append(tool)

        filtered_md_skills: list[dict[str, Any]] = []
        for skill in md_skills_snapshot or []:
            if not isinstance(skill, dict):
                continue
            provider_type = self.provider_type_from_md_skill_snapshot(skill)
            if provider_type and provider_type not in visible_providers:
                continue
            filtered_md_skills.append(skill)

        return filtered_tools, filtered_md_skills

    def permission_entry_tool_group_id(self, entry: dict[str, Any]) -> str:
        """Return the canonical tool group represented by a permission entry."""
        return self.canonical_tool_group_id(
            entry.get("skill_id") or entry.get("skill_name") or entry.get("name")
        )

    def permission_entry_tool_group_members(self, entry: dict[str, Any]) -> list[str]:
        """Return concrete tool IDs covered by a tool-group permission entry."""
        group_id = self.permission_entry_tool_group_id(entry)
        if not group_id:
            return []
        member_ids = entry.get("member_skill_ids")
        source_members = member_ids if isinstance(member_ids, list) else GROUP_TOOLS[group_id]
        members: list[str] = []
        seen: set[str] = set()
        for member in source_members:
            member_id = self.normalize_id(member)
            if member_id and member_id not in seen:
                seen.add(member_id)
                members.append(member_id)
        return members

    def permission_entry_matches_skill(self, entry: dict[str, Any], skill_name: Any) -> bool:
        """Return True when a permission entry applies to a concrete skill/tool."""
        if not isinstance(entry, dict):
            return False
        sid = entry.get("skill_id") or entry.get("skill_name") or ""
        sname = entry.get("skill_name") or sid
        if self.skill_identifier_matches(sid, skill_name) or self.skill_identifier_matches(sname, skill_name):
            return True
        return any(
            self.skill_identifier_matches(member_name, skill_name)
            for member_name in self.permission_entry_tool_group_members(entry)
        )

    def is_permission_entry_enabled(self, entry: dict[str, Any]) -> bool:
        """Return True when a permission entry is both authorized and enabled."""
        return bool(entry.get("authorized", False)) and bool(entry.get("enabled", False))

    def is_skill_enabled(
        self,
        skill_permissions: list[dict[str, Any]],
        skill_name: Any,
    ) -> bool:
        """Return True when a concrete skill/tool is enabled by role permissions."""
        if not isinstance(skill_permissions, list):
            return False
        for entry in skill_permissions:
            if self.permission_entry_matches_skill(entry, skill_name):
                return self.is_permission_entry_enabled(entry)
        return False

    def _tool_group_members_from_snapshot(
        self,
        tools_snapshot: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Build role-facing built-in tool groups from the visible tool snapshot."""
        available_builtin_tools = {
            self.normalize_id(tool.get("name"))
            for tool in tools_snapshot
            if isinstance(tool, dict)
            and self.normalize_id(tool.get("name"))
            and self.is_core_catalog_tool_snapshot(tool)
        }
        result: dict[str, list[str]] = {}
        for group_id in self.role_tool_group_ids:
            members = [
                tool_name
                for tool_name in GROUP_TOOLS.get(group_id, [])
                if tool_name in available_builtin_tools
            ]
            if members:
                result[group_id] = members
        return result

    def tool_group_description(self, group_id: str, members: list[str]) -> str:
        """Build the display description for a role-facing built-in tool group."""
        group_name = group_id.split(":", 1)[-1]
        return f"Built-in tool group: {group_name} ({', '.join(members)})"

    def _build_tool_group_row(
        self,
        group_id: str,
        members: list[str],
        *,
        include_metadata: bool,
    ) -> dict[str, Any]:
        """Build one role catalog row for a built-in tool group."""
        row = {
            "name": group_id,
            "description": self.tool_group_description(group_id, members),
            "category": "tool_group",
            "type": "tool_group",
            "runtime_enabled": True,
            "group_id": group_id,
            "member_skill_ids": list(members),
        }
        if include_metadata:
            row.update(
                {
                    "qualified_name": group_id,
                    "provider_type": "",
                    "group_ids": [group_id, GROUP_ATLASCLAW],
                    "capability_class": group_id,
                    "priority": 100,
                    "location": "built-in",
                    "source": "builtin",
                }
            )
        return row

    def _build_executable_row(self, tool: dict[str, Any], *, include_metadata: bool) -> dict[str, Any]:
        """Build one role catalog row for a standalone executable tool."""
        row = {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "category": tool.get("category", "utility"),
            "type": "executable",
            "runtime_enabled": True,
        }
        if include_metadata:
            row.update(
                {
                    "provider_type": tool.get("provider_type", ""),
                    "group_ids": list(tool.get("group_ids", []) or []),
                    "capability_class": tool.get("capability_class", ""),
                    "priority": int(tool.get("priority", 100) or 100),
                    "location": tool.get("location", "built-in"),
                    "source": tool.get("source", "builtin"),
                }
            )
        return row

    def _build_markdown_row(self, skill: dict[str, Any], *, include_metadata: bool) -> dict[str, Any]:
        """Build one role catalog row for a standalone markdown skill."""
        metadata = skill.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        row = {
            "name": skill["name"],
            "description": skill["description"],
            "category": metadata.get("category", "skill"),
            "type": "markdown",
            "runtime_enabled": skill.get("runtime_enabled", True) is True,
        }
        if include_metadata:
            row.update(
                {
                    "qualified_name": skill.get("qualified_name", skill["name"]),
                    "provider_type": "",
                    "group_ids": list(metadata.get("group_ids", []) or []),
                    "capability_class": metadata.get("capability_class", ""),
                    "priority": int(metadata.get("priority", 100) or 100),
                    "location": skill.get("location", "built-in"),
                    "file_path": skill.get("file_path", ""),
                }
            )
        return row

    def build_role_skill_catalog(
        self,
        *,
        tools_snapshot: list[dict[str, Any]],
        md_skills: list[dict[str, Any]],
        include_metadata: bool = False,
    ) -> list[dict[str, Any]]:
        """Build the role-management skill catalog from runtime snapshots."""
        rows: list[dict[str, Any]] = []

        tool_groups = self._tool_group_members_from_snapshot(tools_snapshot)
        covered_tools = {
            tool_name
            for members in tool_groups.values()
            for tool_name in members
        }
        for group_id in self.role_tool_group_ids:
            members = tool_groups.get(group_id, [])
            if members:
                rows.append(
                    self._build_tool_group_row(
                        group_id,
                        members,
                        include_metadata=include_metadata,
                    )
                )

        for tool in tools_snapshot:
            if not isinstance(tool, dict):
                continue
            tool_name = self.normalize_id(tool.get("name"))
            if not tool_name or tool_name in covered_tools:
                continue
            if not self.is_core_catalog_tool_snapshot(tool):
                continue
            rows.append(self._build_executable_row(tool, include_metadata=include_metadata))

        for skill in md_skills:
            if not isinstance(skill, dict) or self.is_provider_bound_md_skill_snapshot(skill):
                continue
            if not self.normalize_id(skill.get("name")) or not self.normalize_id(skill.get("description")):
                continue
            rows.append(self._build_markdown_row(skill, include_metadata=include_metadata))

        return rows

    def collect_provider_bound_skill_ids(self, skill_registry: Any) -> set[str]:
        """Return concrete IDs that should be governed by provider permissions."""
        if skill_registry is None:
            return set()

        provider_bound: set[str] = set()
        try:
            tools_snapshot = skill_registry.tools_snapshot()
        except Exception:
            tools_snapshot = []

        for tool in tools_snapshot:
            if not isinstance(tool, dict) or not self.is_provider_bound_tool_snapshot(tool):
                continue
            for value in (
                tool.get("name"),
                tool.get("skill_name"),
                tool.get("qualified_skill_name"),
            ):
                normalized = self.normalize_key(value)
                if normalized:
                    provider_bound.add(normalized)

        try:
            md_snapshot = skill_registry.md_snapshot()
        except Exception:
            md_snapshot = []
        provider_md_qualified: set[str] = set()
        for skill in md_snapshot:
            if not isinstance(skill, dict) or not self.is_provider_bound_md_skill_snapshot(skill):
                continue
            for value in (skill.get("name"), skill.get("qualified_name")):
                normalized = self.normalize_key(value)
                if normalized:
                    provider_bound.add(normalized)
            qualified_name = self.normalize_id(skill.get("qualified_name"))
            if qualified_name:
                provider_md_qualified.add(qualified_name)

        md_skill_tools = getattr(skill_registry, "_md_skill_tools", {})
        if isinstance(md_skill_tools, dict):
            for qualified_name in provider_md_qualified:
                for tool_name in md_skill_tools.get(qualified_name, []) or []:
                    normalized = self.normalize_key(tool_name)
                    if normalized:
                        provider_bound.add(normalized)

        return provider_bound

    def expand_role_skill_permissions_for_storage(
        self,
        permissions: dict[str, Any] | None,
        *,
        skill_registry: Any = None,
    ) -> dict[str, Any] | None:
        """Expand role-facing grouped skill permissions into concrete storage rows."""
        if not isinstance(permissions, dict):
            return permissions

        result = deepcopy(permissions)
        skills_section = result.get("skills")
        if not isinstance(skills_section, dict):
            return result

        entries = skills_section.get("skill_permissions")
        if not isinstance(entries, list):
            return result

        provider_bound = self.collect_provider_bound_skill_ids(skill_registry)
        expanded: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _append(entry: dict[str, Any], skill_id: str, skill_name: str) -> None:
            normalized_id = self.normalize_id(skill_id)
            if not normalized_id:
                return
            key = self.normalize_key(normalized_id)
            if key in seen:
                return
            seen.add(key)
            expanded.append(
                {
                    "skill_id": normalized_id,
                    "skill_name": self.normalize_id(skill_name) or normalized_id,
                    "description": self.normalize_id(entry.get("description")),
                    "authorized": bool(entry.get("authorized", False)),
                    "enabled": bool(entry.get("enabled", False)),
                }
            )

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            skill_id = self.normalize_id(entry.get("skill_id") or entry.get("name") or entry.get("skill_name"))
            skill_name = self.normalize_id(entry.get("skill_name") or skill_id)
            normalized_key = self.normalize_key(skill_id)
            if not skill_id:
                continue
            if normalized_key in provider_bound:
                continue

            group_id = self.canonical_tool_group_id(skill_id)
            if ":" in skill_id and not group_id:
                continue
            if group_id:
                for member in self.permission_entry_tool_group_members(entry):
                    if self.normalize_key(member) not in provider_bound:
                        _append(entry, member, member)
                continue
            _append(entry, skill_id, skill_name)

        skills_section["skill_permissions"] = expanded
        return result

    def collapse_role_skill_permissions_for_response(
        self,
        permissions: dict[str, Any] | None,
        *,
        skill_registry: Any = None,
    ) -> dict[str, Any]:
        """Collapse concrete storage rows into role-facing grouped permission rows."""
        result = deepcopy(permissions) if isinstance(permissions, dict) else {}
        skills_section = result.get("skills")
        if not isinstance(skills_section, dict):
            return result

        entries = skills_section.get("skill_permissions")
        if not isinstance(entries, list):
            return result

        provider_bound = self.collect_provider_bound_skill_ids(skill_registry)
        member_to_group = {
            tool_name: group_id
            for group_id in self.role_tool_group_ids
            for tool_name in GROUP_TOOLS.get(group_id, [])
        }
        grouped_entries: dict[str, dict[str, dict[str, Any]]] = {
            group_id: {} for group_id in self.role_tool_group_ids
        }
        retained: list[dict[str, Any]] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            skill_id = self.normalize_id(entry.get("skill_id") or entry.get("skill_name"))
            skill_name = self.normalize_id(entry.get("skill_name") or skill_id)
            normalized_key = self.normalize_key(skill_id)
            if not skill_id:
                continue
            if normalized_key in provider_bound:
                continue

            group_id = self.canonical_tool_group_id(skill_id)
            if ":" in skill_id and not group_id:
                continue
            if group_id:
                for member in self.permission_entry_tool_group_members(entry):
                    grouped_entries.setdefault(group_id, {})[member] = entry
                continue
            member_group_id = member_to_group.get(skill_id)
            if member_group_id:
                grouped_entries.setdefault(member_group_id, {})[skill_id] = entry
                continue
            retained.append(
                {
                    "skill_id": skill_id,
                    "skill_name": skill_name or skill_id,
                    "description": self.normalize_id(entry.get("description")),
                    "authorized": bool(entry.get("authorized", False)),
                    "enabled": bool(entry.get("enabled", False)),
                }
            )

        collapsed: list[dict[str, Any]] = []
        for group_id in self.role_tool_group_ids:
            member_entries = grouped_entries.get(group_id, {})
            if not member_entries:
                continue
            members = list(GROUP_TOOLS.get(group_id, []))
            complete = all(member in member_entries for member in members)
            authorized_values = [
                bool(member_entries[member].get("authorized", False))
                for member in members
                if member in member_entries
            ]
            enabled_values = [
                bool(member_entries[member].get("enabled", False))
                for member in members
                if member in member_entries
            ]
            authorized = complete and all(authorized_values)
            enabled = complete and all(enabled_values)
            partial = (
                not complete
                or len(set(authorized_values)) > 1
                or len(set(enabled_values)) > 1
            )
            collapsed.append(
                {
                    "skill_id": group_id,
                    "skill_name": group_id,
                    "description": self.tool_group_description(group_id, members),
                    "authorized": authorized,
                    "enabled": enabled,
                    "type": "tool_group",
                    "group_id": group_id,
                    "member_skill_ids": members,
                    "runtime_enabled": True,
                    "partial": partial,
                }
            )

        skills_section["skill_permissions"] = collapsed + retained
        return result


skill_permission_service = SkillPermissionService()
