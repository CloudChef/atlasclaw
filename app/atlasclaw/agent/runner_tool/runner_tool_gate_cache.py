from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any, Optional

from app.atlasclaw.agent.tool_gate_models import ToolIntentPlan


class RunnerToolGateCacheMixin:
    @staticmethod
    def _build_toolset_signature(available_tools: list[dict[str, Any]]) -> str:
        signatures: list[str] = []
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", "") or "").strip()
            if not name:
                continue
            capability = str(tool.get("capability_class", "") or "").strip().lower()
            provider_type = str(tool.get("provider_type", "") or "").strip().lower()
            signatures.append(f"{name}|{capability}|{provider_type}")
        signatures.sort()
        return "\n".join(signatures)

    def _build_tool_gate_cache_key(
        self,
        *,
        session_key: str,
        resolved_tool_request: str,
        used_follow_up_context: bool,
        recent_history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
        metadata_candidates: Optional[dict[str, Any]] = None,
    ) -> str:
        history_parts: list[str] = []
        if used_follow_up_context:
            for item in recent_history[-4:]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "") or "").strip()
                content = " ".join(str(item.get("content", "") or "").split()).strip()
                if role and content:
                    history_parts.append(f"{role}:{content}")
        metadata_signature = ""
        if isinstance(metadata_candidates, dict):
            metadata_signature = json.dumps(
                {
                    "providers": list(metadata_candidates.get("preferred_provider_types", []) or []),
                    "groups": list(metadata_candidates.get("preferred_group_ids", []) or []),
                    "capabilities": list(
                        metadata_candidates.get("preferred_capability_classes", []) or []
                    ),
                    "tools": list(metadata_candidates.get("preferred_tool_names", []) or []),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        payload = "\n".join(
            [
                str(session_key or "").strip(),
                " ".join(str(resolved_tool_request or "").split()).strip(),
                "1" if used_follow_up_context else "0",
                "\n".join(history_parts),
                self._build_toolset_signature(available_tools),
                metadata_signature,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get_cached_tool_intent_plan(self, cache_key: str) -> Optional[ToolIntentPlan]:
        cache = getattr(self, "_tool_intent_plan_cache", None)
        if not isinstance(cache, OrderedDict):
            return None
        entry = cache.get(cache_key)
        if not entry:
            return None
        expires_at, payload = entry
        if float(expires_at) <= time.monotonic():
            cache.pop(cache_key, None)
            return None
        cache.move_to_end(cache_key)
        try:
            return ToolIntentPlan.model_validate(dict(payload))
        except Exception:
            cache.pop(cache_key, None)
            return None

    def _store_tool_intent_plan_cache(
        self,
        *,
        cache_key: str,
        plan: ToolIntentPlan,
    ) -> None:
        cache = getattr(self, "_tool_intent_plan_cache", None)
        if not isinstance(cache, OrderedDict):
            return
        ttl_seconds = max(
            1.0,
            float(getattr(self, "TOOL_INTENT_PLAN_CACHE_TTL_SECONDS", 300.0) or 300.0),
        )
        max_entries = max(
            32,
            int(getattr(self, "TOOL_INTENT_PLAN_CACHE_MAX_ENTRIES", 512) or 512),
        )
        now = time.monotonic()
        expires_at = now + ttl_seconds
        cache[cache_key] = (expires_at, plan.model_dump(mode="python"))
        cache.move_to_end(cache_key)
        stale_keys = [key for key, (expire_ts, _) in list(cache.items()) if float(expire_ts) <= now]
        for key in stale_keys:
            cache.pop(key, None)
        while len(cache) > max_entries:
            cache.popitem(last=False)

