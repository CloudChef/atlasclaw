"""History normalization and long-term memory coordination for agent runs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, TextPart, UserPromptPart

from app.xuanwu.agent.compaction import CompactionPipeline
from app.xuanwu.core.deps import SkillDeps


class HistoryMemoryCoordinator:
    """Encapsulates transcript conversion and long-term memory file management."""

    COMPACTION_SUMMARY_PREFIX = "[Compression Summary - Earlier conversation has been summarized]"
    MEMORY_RECALL_PREFIX = "[Long-term Memory Recall]"

    def __init__(self, session_manager: Any, compaction: CompactionPipeline) -> None:
        self.sessions = session_manager
        self.compaction = compaction

    def normalize_messages(self, messages: list[Any]) -> list[dict]:
        """Normalize agent messages into session-manager dictionaries."""
        normalized: list[dict] = []
        for msg in messages or []:
            if isinstance(msg, dict):
                item = dict(msg)
                item.setdefault("role", "assistant")
                item.setdefault("content", "")
                normalized.append(item)
                continue

            role = self._extract_message_role(msg)
            content = self._extract_message_content(msg)
            item = {
                "role": str(role),
                "content": content if isinstance(content, str) else str(content),
            }
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                normalized_tool_calls = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        normalized_tool_calls.append(tc)
                    else:
                        normalized_tool_calls.append(
                            {
                                "id": getattr(tc, "id", ""),
                                "name": getattr(tc, "name", getattr(tc, "tool_name", "")),
                                "args": getattr(tc, "args", {}),
                            }
                        )
                item["tool_calls"] = normalized_tool_calls
            normalized.append(item)
        return normalized

    def build_message_history(self, transcript: list[Any]) -> list[dict]:
        """Convert transcript entries into normalized messages."""
        messages = []
        for entry in transcript:
            msg = {
                "role": entry.role,
                "content": entry.content,
            }
            if entry.tool_calls:
                msg["tool_calls"] = entry.tool_calls
            messages.append(msg)
        return messages

    def to_model_message_history(self, messages: list[dict]) -> list[Any]:
        """Convert normalized transcript messages into PydanticAI model messages."""
        model_messages: list[Any] = []
        for message in messages:
            role = str(message.get("role", "")).strip().lower()
            content = str(message.get("content", "")).strip()

            if role == "user":
                if content:
                    model_messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
                continue

            if role == "system":
                if content:
                    model_messages.append(ModelRequest(parts=[SystemPromptPart(content=content)]))
                continue

            if role in {"assistant", "tool"} and content:
                model_messages.append(ModelResponse(parts=[TextPart(content=content)]))
        return model_messages

    def prune_summary_messages(self, messages: list[dict]) -> list[dict]:
        """Remove previously injected summary/recall system messages from session context."""
        pruned: list[dict] = []
        for msg in messages:
            if msg.get("role") != "system":
                pruned.append(msg)
                continue
            content = str(msg.get("content", ""))
            if content.startswith(self.COMPACTION_SUMMARY_PREFIX):
                continue
            if content.startswith(self.MEMORY_RECALL_PREFIX):
                continue
            pruned.append(msg)
        return pruned

    async def flush_history_to_timestamped_memory(
        self,
        *,
        session_key: str,
        messages: list[dict],
        deps: SkillDeps,
        session: Any,
        context_window: Optional[int],
        flushed_signatures: set[str],
    ) -> None:
        """Summarize overflow history and write to workspace/users/<userId>/memory/memory_<timestamp>.md."""
        summary = await self.compaction.summarize_overflow(messages)
        summary = summary.strip()
        if not summary:
            return

        signature = summary[:500]
        if signature in flushed_signatures:
            return
        flushed_signatures.add(signature)

        user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
        workspace_root = Path(str(getattr(self.sessions, "workspace_path", "."))).resolve()
        user_memory_dir = workspace_root / "users" / user_id / "memory"
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_path = user_memory_dir / f"memory_{file_timestamp}.md"

        estimated_tokens = self.compaction.estimate_tokens(messages)
        lines = [
            "# Memory Snapshot",
            "",
            f"- timestamp_utc: {datetime.now(timezone.utc).isoformat()}",
            f"- user_id: {user_id}",
            f"- session_key: {session_key}",
            f"- estimated_tokens_before: {estimated_tokens}",
            f"- context_window: {context_window or self.compaction.config.context_window}",
            "",
            "## Summary",
            "",
            summary,
            "",
        ]
        payload = "\n".join(lines)

        def _write() -> None:
            user_memory_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(payload, encoding="utf-8")

        await asyncio.to_thread(_write)

        if hasattr(session, "memory_flushed_this_cycle"):
            session.memory_flushed_this_cycle = True

    async def inject_memory_recall(self, messages: list[dict], deps: SkillDeps) -> list[dict]:
        """Load recent memory_*.md files and inject one recall system message."""
        user_id = getattr(getattr(deps, "user_info", None), "user_id", "") or "default"
        workspace_root = Path(str(getattr(self.sessions, "workspace_path", "."))).resolve()
        user_memory_dir = workspace_root / "users" / user_id / "memory"

        def _read_recent() -> list[tuple[str, str]]:
            if not user_memory_dir.exists():
                return []
            files = sorted(user_memory_dir.glob("memory_*.md"), reverse=True)[:3]
            result: list[tuple[str, str]] = []
            for fp in files:
                try:
                    text = fp.read_text(encoding="utf-8").strip()
                except Exception:
                    continue
                if not text:
                    continue
                result.append((fp.name, text[:1200]))
            return result

        recent = await asyncio.to_thread(_read_recent)
        if not recent:
            return messages

        recall_lines = [self.MEMORY_RECALL_PREFIX, ""]
        for name, excerpt in recent:
            recall_lines.append(f"### {name}")
            recall_lines.append(excerpt)
            recall_lines.append("")

        recall_message = {
            "role": "system",
            "content": "\n".join(recall_lines).strip(),
        }

        cleaned: list[dict] = []
        for msg in messages:
            if msg.get("role") == "system" and str(msg.get("content", "")).startswith(self.MEMORY_RECALL_PREFIX):
                continue
            cleaned.append(msg)
        if cleaned and cleaned[0].get("role") == "system":
            return [cleaned[0], recall_message, *cleaned[1:]]
        return [recall_message, *cleaned]

    def _extract_message_role(self, msg: Any) -> str:
        role = getattr(msg, "role", None)
        if isinstance(role, str) and role:
            return role

        kind = getattr(msg, "kind", "")
        if kind == "request":
            parts = getattr(msg, "parts", None) or []
            if any(getattr(part, "part_kind", "") == "system-prompt" for part in parts):
                return "system"
            return "user"
        if kind == "response":
            return "assistant"
        return "assistant"

    def _extract_message_content(self, msg: Any) -> str:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content

        parts = getattr(msg, "parts", None)
        if not parts:
            return "" if content is None else str(content)

        chunks: list[str] = []
        for part in parts:
            part_kind = getattr(part, "part_kind", "")
            part_content = getattr(part, "content", None)
            if part_kind == "thinking":
                continue
            if part_kind in {"text", "user-prompt", "system-prompt", ""}:
                if isinstance(part_content, str) and part_content:
                    chunks.append(part_content)
                elif isinstance(part_content, (list, tuple)):
                    chunks.extend(str(item) for item in part_content if item)
                elif part_content:
                    chunks.append(str(part_content))
        return "".join(chunks)
