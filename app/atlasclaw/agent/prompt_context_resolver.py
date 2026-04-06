# -*- coding: utf-8 -*-
"""Session-aware prompt context resolver with budget controls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ResolvedPromptFile:
    """Resolved prompt file content ready for prompt injection."""

    filename: str
    path: Path
    content: str
    truncated: bool
    skipped_reason: Optional[str] = None


class PromptContextResolver:
    """Resolve bootstrap/context files with session filters and budget limits."""

    INCLUDE_MARKER = "<!-- atlasclaw-session-include:"
    EXCLUDE_MARKER = "<!-- atlasclaw-session-exclude:"

    def resolve(
        self,
        *,
        workspace: Path,
        filenames: list[str],
        session_key: Optional[str],
        total_budget: int,
        per_file_budget: int,
    ) -> list[ResolvedPromptFile]:
        normalized_total_budget = max(0, int(total_budget or 0))
        normalized_per_file_budget = max(0, int(per_file_budget or 0))
        remaining_budget = normalized_total_budget
        resolved: list[ResolvedPromptFile] = []

        for filename in filenames:
            file_path = workspace / filename
            if not file_path.exists():
                continue
            try:
                raw_content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            content = self._strip_control_markers(raw_content)
            include_tokens = self._read_marker_tokens(raw_content, self.INCLUDE_MARKER)
            exclude_tokens = self._read_marker_tokens(raw_content, self.EXCLUDE_MARKER)
            if not self._is_file_allowed(
                session_key=session_key or "",
                include_tokens=include_tokens,
                exclude_tokens=exclude_tokens,
            ):
                continue

            truncated = False
            if normalized_per_file_budget > 0 and len(content) > normalized_per_file_budget:
                content = content[:normalized_per_file_budget]
                truncated = True

            if normalized_total_budget > 0:
                if remaining_budget <= 0:
                    break
                if len(content) > remaining_budget:
                    content = content[:remaining_budget]
                    truncated = True
                remaining_budget -= len(content)

            if not content:
                continue
            resolved.append(
                ResolvedPromptFile(
                    filename=filename,
                    path=file_path,
                    content=content,
                    truncated=truncated,
                )
            )
        return resolved

    @classmethod
    def _read_marker_tokens(cls, content: str, marker_prefix: str) -> list[str]:
        tokens: list[str] = []
        for line in (content or "").splitlines()[:12]:
            stripped = line.strip()
            if not stripped.startswith(marker_prefix):
                continue
            payload = stripped[len(marker_prefix) :]
            payload = payload.split("-->", 1)[0].strip()
            for token in payload.split(","):
                normalized = token.strip()
                if normalized:
                    tokens.append(normalized)
        return tokens

    @classmethod
    def _strip_control_markers(cls, content: str) -> str:
        filtered: list[str] = []
        for line in (content or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(cls.INCLUDE_MARKER) or stripped.startswith(cls.EXCLUDE_MARKER):
                continue
            filtered.append(line)
        return "\n".join(filtered).strip()

    @staticmethod
    def _is_file_allowed(
        *,
        session_key: str,
        include_tokens: list[str],
        exclude_tokens: list[str],
    ) -> bool:
        lowered_key = (session_key or "").lower()
        if include_tokens:
            include_match = any(token.lower() in lowered_key for token in include_tokens)
            if not include_match:
                return False
        if exclude_tokens:
            exclude_match = any(token.lower() in lowered_key for token in exclude_tokens)
            if exclude_match:
                return False
        return True
