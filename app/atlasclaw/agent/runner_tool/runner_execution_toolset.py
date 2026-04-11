from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any

from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.tools.policy_pipeline import ToolPolicyPipeline, build_ordered_policy_layers


class RunnerExecutionToolsetMixin:
    def _build_turn_toolset(
        self,
        *,
        deps: SkillDeps,
        session_key: str,
        all_tools: list[dict[str, Any]],
        tool_groups: dict[str, list[str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        """Filter runtime tools through an ordered allow/deny policy pipeline."""
        if not all_tools:
            return [], [], False

        policy = self._resolve_toolset_policy_payload(deps)
        provider_type = ""
        if isinstance(getattr(deps, "extra", None), dict):
            provider_instance = deps.extra.get("provider_instance")
            if isinstance(provider_instance, dict):
                provider_type = str(provider_instance.get("provider_type", "")).strip()
        cache_key = self._build_turn_toolset_cache_key(
            session_key=session_key,
            policy=policy,
            provider_type=provider_type,
            all_tools=all_tools,
            tool_groups=tool_groups,
        )
        cached = self._get_cached_turn_toolset(cache_key)
        if cached is not None:
            return cached

        layers = build_ordered_policy_layers(
            policy=policy,
            provider_type=provider_type,
            agent_id=getattr(self, "agent_id", "") or "",
            channel=str(getattr(deps, "channel", "") or ""),
            session_key=session_key,
        )
        pipeline = ToolPolicyPipeline(
            tools=all_tools,
            group_map=tool_groups,
            aliases=self._build_tool_aliases(all_tools),
        )
        result = pipeline.run(layers)

        filtered_names = set(result.tool_names)
        filtered_tools = [
            tool
            for tool in all_tools
            if str(tool.get("name", "")).strip() in filtered_names
        ]
        payload = (filtered_tools, result.trace, False)
        self._store_turn_toolset_cache(cache_key=cache_key, payload=payload)
        return payload
    def _build_turn_toolset_cache_key(
        self,
        *,
        session_key: str,
        policy: dict[str, Any],
        provider_type: str,
        all_tools: list[dict[str, Any]],
        tool_groups: dict[str, list[str]],
    ) -> str:
        policy_blob = json.dumps(policy, ensure_ascii=False, sort_keys=True)
        toolset_signature = self._build_toolset_signature(all_tools)
        group_rows = []
        for group_id in sorted((tool_groups or {}).keys()):
            members = sorted(
                str(item).strip()
                for item in (tool_groups.get(group_id) or [])
                if str(item).strip()
            )
            group_rows.append(f"{group_id}:{','.join(members)}")
        payload = "\n".join(
            [
                str(session_key or "").strip(),
                str(provider_type or "").strip().lower(),
                policy_blob,
                toolset_signature,
                "\n".join(group_rows),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    def _get_cached_turn_toolset(
        self,
        cache_key: str,
    ) -> Optional[tuple[list[dict[str, Any]], list[dict[str, Any]], bool]]:
        cache = getattr(self, "_turn_toolset_cache", None)
        if not isinstance(cache, OrderedDict):
            return None
        entry = cache.get(cache_key)
        if not entry:
            return None
        expires_at, tools, trace, used_fallback = entry
        if float(expires_at) <= time.monotonic():
            cache.pop(cache_key, None)
            return None
        cache.move_to_end(cache_key)
        return (
            [dict(item) for item in tools if isinstance(item, dict)],
            [dict(item) for item in trace if isinstance(item, dict)],
            bool(used_fallback),
        )
    def _store_turn_toolset_cache(
        self,
        *,
        cache_key: str,
        payload: tuple[list[dict[str, Any]], list[dict[str, Any]], bool],
    ) -> None:
        cache = getattr(self, "_turn_toolset_cache", None)
        if not isinstance(cache, OrderedDict):
            return
        ttl_seconds = max(
            1.0,
            float(getattr(self, "TURN_TOOLSET_CACHE_TTL_SECONDS", 300.0) or 300.0),
        )
        max_entries = max(
            32,
            int(getattr(self, "TURN_TOOLSET_CACHE_MAX_ENTRIES", 256) or 256),
        )
        expires_at = time.monotonic() + ttl_seconds
        tools, trace, used_fallback = payload
        cache[cache_key] = (
            expires_at,
            [dict(item) for item in tools if isinstance(item, dict)],
            [dict(item) for item in trace if isinstance(item, dict)],
            bool(used_fallback),
        )
        cache.move_to_end(cache_key)
        now = time.monotonic()
        stale_keys = [key for key, (expire_ts, *_rest) in list(cache.items()) if float(expire_ts) <= now]
        for key in stale_keys:
            cache.pop(key, None)
        while len(cache) > max_entries:
            cache.popitem(last=False)
    @staticmethod
    def _resolve_toolset_policy_payload(deps: SkillDeps) -> dict[str, Any]:
        extra = deps.extra if isinstance(getattr(deps, "extra", None), dict) else {}
        payload = extra.get("toolset_policy")
        if isinstance(payload, dict):
            return payload
        return {}
    @staticmethod
    def _build_tool_aliases(tools: list[dict[str, Any]]) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        capability_map: dict[str, list[str]] = {}
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", "")).strip()
            if not name:
                continue
            capability = str(tool.get("capability_class", "")).strip()
            if capability:
                capability_map.setdefault(capability, []).append(name)
        for capability, members in capability_map.items():
            aliases[capability] = members
            if capability.startswith("provider:"):
                provider_type = capability.split(":", 1)[1].strip()
                if provider_type:
                    aliases.setdefault(f"group:{provider_type}", members)
        return aliases
    @staticmethod
    def _build_filtered_group_map(
        original_groups: dict[str, list[str]],
        filtered_tools: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        allowed = {
            str(tool.get("name", "")).strip()
            for tool in filtered_tools
            if isinstance(tool, dict) and str(tool.get("name", "")).strip()
        }
        filtered: dict[str, list[str]] = {}
        for group_id, members in (original_groups or {}).items():
            normalized_group = str(group_id or "").strip()
            if not normalized_group:
                continue
            kept = [
                str(member).strip()
                for member in (members or [])
                if str(member).strip() in allowed
            ]
            if kept:
                filtered[normalized_group] = kept
        return filtered

