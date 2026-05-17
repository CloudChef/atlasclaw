# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Markdown-backed long-term memory persistence for AtlasClaw.

The memory manager stores user profile and preference memory in one Markdown
file under the user workspace. It also provides read, search, and parsing
helpers for that file.

Storage layout::

    users/<user_id>/memory/MEMORY.md       # long-term memory
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import aiofiles

from app.atlasclaw.core.security_guard import encode_if_untrusted
from app.atlasclaw.core.user_paths import user_runtime_dir

_HOOK_MEMORY_METADATA_PREFIXES = (
    "- timestamp_utc:",
    "- module_name:",
    "- user_id:",
    "- source_event_ids:",
)
LONG_TERM_PREFERENCES_SECTION = "Preferences"


class MemoryType(Enum):
    """Memory storage category."""
    LONG_TERM = "long_term"


@dataclass
class MemoryEntry:
    """
    Structured memory entry.

    Attributes:
        id: Stable entry identifier.
        content: Memory content.
        memory_type: Memory storage category.
        source: Source identifier, such as a session or agent.
        timestamp: Creation time.
        tags: Optional tag list.
        embedding: Optional embedding vector.
        metadata: Additional metadata associated with the entry.
    """
    id: str
    content: str
    memory_type: MemoryType = MemoryType.LONG_TERM
    source: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def generate_id(cls, content: str, timestamp: datetime) -> str:
        """Generate a stable short ID for a memory entry."""
        hash_input = f"{content[:100]}{timestamp.isoformat()}"
        return hashlib.md5(hash_input.encode()).hexdigest()[:12]


class MemoryManager:
    """
    Manager for one user-scoped long-term Markdown memory file.

    All read, write, and search operations are restricted to
    ``users/<user_id>/memory/MEMORY.md``. Date-scoped and session snapshot
    files are intentionally not part of the memory contract.
    """
    
    def __init__(
        self,
        workspace: str,
        *,
        long_term_file: str = "MEMORY.md",
        user_id: str = "default",
        encoding: str = "utf-8",
    ) -> None:
        """
        Initialize the memory manager.

        Args:
            workspace: Workspace root path.
            long_term_file: File name for long-term memory storage.
            user_id: User identifier for per-user storage isolation.
            encoding: File encoding used for all memory files.
        """
        self._workspace = Path(workspace)
        self._user_id = user_id
        self._long_term_file = long_term_file
        self._memory_dir = user_runtime_dir(self._workspace, user_id) / "memory"
        self._long_term_path = self._memory_dir / long_term_file
        self._encoding = encoding

        # Serialize writes across concurrent tasks.
        self._write_lock = asyncio.Lock()
        
    @property
    def workspace_path(self) -> Path:
        """Return the workspace root used to resolve user memory paths."""
        return self._workspace

    @property
    def memory_dir(self) -> Path:
        """Return the directory used for this user's Markdown memory files."""
        return self._memory_dir
        
    @property
    def long_term_path(self) -> Path:
        """Return the long-term memory file path."""
        return self._long_term_path

    def for_user(self, user_id: str) -> "MemoryManager":
        """Return a new manager for another user in the same workspace.

        The startup/runtime manager is used as a workspace template. Callers
        must derive a per-request manager through this method before reading or
        writing memory so user directories remain isolated.
        """
        return MemoryManager(
            workspace=str(self._workspace),
            long_term_file=self._long_term_file,
            user_id=user_id or "default",
            encoding=self._encoding,
        )
        
    async def ensure_dirs(self) -> None:
        """Ensure the current user's long-term memory directory exists."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        
    async def write_long_term(
        self,
        content: str,
        *,
        source: str = "",
        tags: Optional[list[str]] = None,
        section: str = "General",
    ) -> MemoryEntry:
        """
        Write a long-term memory entry into `MEMORY.md`.

        Args:
            content: Memory content.
            source: Source identifier.
            tags: Optional tag list.
            section: Target section name in `MEMORY.md`.

        Returns:
            The created memory entry.
        """
        timestamp = datetime.now(timezone.utc)
        
        safe_content, encoded = encode_if_untrusted(content)
        entry = MemoryEntry(
            id=MemoryEntry.generate_id(content, timestamp),
            content=safe_content,
            memory_type=MemoryType.LONG_TERM,
            source=source,
            timestamp=timestamp,
            tags=(tags or []) + (["encoded_input"] if encoded else []),
            metadata={"section": section}
        )
        
        async with self._write_lock:
            self._long_term_path.parent.mkdir(parents=True, exist_ok=True)
            
            existing_content = ""
            if self._long_term_path.exists():
                async with aiofiles.open(self._long_term_path, 'r', encoding=self._encoding) as f:
                    existing_content = await f.read()
                    
            updated_content = self._update_long_term_content(
                existing_content, entry, section
            )
            
            async with aiofiles.open(self._long_term_path, 'w', encoding=self._encoding) as f:
                await f.write(updated_content)
        
        return entry

    async def replace_long_term_section(
        self,
        contents: list[str],
        *,
        source: str = "",
        tags: Optional[list[str]] = None,
        section: str = "General",
    ) -> list[MemoryEntry]:
        """
        Replace one long-term Markdown section with a maintained preference list.

        Args:
            contents: Final section entries in display order.
            source: Source identifier applied to returned entries.
            tags: Optional tag list applied to returned entries.
            section: Target section name in ``MEMORY.md``.

        Returns:
            Memory entries corresponding to the written section lines.
        """
        timestamp = datetime.now(timezone.utc)
        entries: list[MemoryEntry] = []
        safe_contents: list[str] = []
        for content in contents:
            normalized = str(content or "").strip()
            if not normalized:
                continue
            safe_content, encoded = encode_if_untrusted(normalized)
            safe_contents.append(safe_content)
            entries.append(
                MemoryEntry(
                    id=MemoryEntry.generate_id(normalized, timestamp),
                    content=safe_content,
                    memory_type=MemoryType.LONG_TERM,
                    source=source,
                    timestamp=timestamp,
                    tags=(tags or []) + (["encoded_input"] if encoded else []),
                    metadata={"section": section},
                )
            )

        async with self._write_lock:
            self._long_term_path.parent.mkdir(parents=True, exist_ok=True)
            existing_content = ""
            if self._long_term_path.exists():
                async with aiofiles.open(self._long_term_path, 'r', encoding=self._encoding) as f:
                    existing_content = await f.read()

            updated_content = self._replace_long_term_section_content(
                existing_content,
                safe_contents,
                section,
            )

            async with aiofiles.open(self._long_term_path, 'w', encoding=self._encoding) as f:
                await f.write(updated_content)

        return entries
        
    def _update_long_term_content(
        self,
        existing: str,
        entry: MemoryEntry,
        section: str
    ) -> str:
        """Insert a long-term entry into the requested Markdown section."""
        if not existing:
            return f"# Long-term Memory\n\n## {section}\n\n{entry.content}\n"
            
        section_pattern = rf"(## {re.escape(section)}\n)"
        match = re.search(section_pattern, existing)
        
        if match:
            insert_pos = match.end()
            next_section = re.search(r"\n## ", existing[insert_pos:])
            if next_section:
                insert_pos += next_section.start()
            else:
                insert_pos = len(existing)
                
            return (
                existing[:insert_pos].rstrip() +
                f"\n\n{entry.content}\n" +
                existing[insert_pos:]
            )
        else:
            return existing.rstrip() + f"\n\n## {section}\n\n{entry.content}\n"

    def _replace_long_term_section_content(
        self,
        existing: str,
        contents: list[str],
        section: str,
    ) -> str:
        """Replace one Markdown section while preserving unrelated sections."""
        section_body = "\n\n".join(content.strip() for content in contents if content.strip())
        replacement = f"## {section}\n"
        if section_body:
            replacement += f"\n{section_body}\n"

        if not existing:
            return f"# Long-term Memory\n\n{replacement}"

        section_pattern = rf"^## {re.escape(section)}\s*$"
        match = re.search(section_pattern, existing, flags=re.MULTILINE)
        if not match:
            return existing.rstrip() + f"\n\n{replacement}"

        next_section = re.search(r"^## .*$", existing[match.end():], flags=re.MULTILINE)
        end_pos = match.end() + next_section.start() if next_section else len(existing)
        prefix = existing[:match.start()].rstrip()
        suffix = existing[end_pos:].lstrip("\n").rstrip()
        parts = [part for part in (prefix, replacement.rstrip(), suffix) if part]
        return "\n\n".join(parts) + "\n"
            
    async def search(
        self,
        query: str,
        limit: int = 10,
        *,
        apply_recency: bool = True,
    ) -> list[Any]:
        """
        Search the current user's long-term memory file.

        Results are compatible with the built-in ``memory_search`` tool: each
        returned item includes a ``MemoryEntry`` whose metadata carries
        ``path``, ``start_line``, and ``end_line`` citation fields.
        """
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        await self.ensure_dirs()
        entries = await self._load_search_entries()
        if not entries:
            return []

        from app.atlasclaw.memory.search import HybridSearcher

        safe_limit = limit if isinstance(limit, int) and limit > 0 else 10
        searcher = HybridSearcher(user_id=self._user_id, workspace=str(self._workspace))
        for entry in entries:
            searcher.index_sync(entry)

        results = await searcher.search(
            normalized_query,
            top_k=safe_limit,
            apply_recency=apply_recency,
        )
        return [result for result in results if result.score > 0]

    async def get(
        self,
        path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Read a slice from the current user's ``MEMORY.md`` file.

        Args:
            path: Absolute path, workspace-relative path, or memory-dir-relative
                path. Only the current user's ``MEMORY.md`` is accepted.
            offset: Optional zero-based line offset.
            limit: Optional maximum number of lines to return.

        Returns:
            Dictionary with ``content``, ``path``, ``start_line``, and
            ``end_line`` fields for tool citation formatting.
        """
        await self.ensure_dirs()
        target_path = self._resolve_memory_file(path)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError(f"Memory file not found: {path}")

        async with aiofiles.open(target_path, 'r', encoding=self._encoding) as f:
            content = await f.read()

        lines = content.splitlines(keepends=True)
        start_index = offset if isinstance(offset, int) and offset >= 0 else 0
        if isinstance(limit, int) and limit > 0:
            selected = lines[start_index:start_index + limit]
        else:
            selected = lines[start_index:]

        start_line = start_index + 1
        end_line = start_line + len(selected) - 1 if selected else start_line

        return {
            "content": "".join(selected).rstrip("\n"),
            "path": self.display_path(target_path),
            "start_line": start_line,
            "end_line": end_line,
        }

    async def _load_search_entries(self) -> list[MemoryEntry]:
        """Load searchable chunks from the user's long-term memory file."""
        entries: list[MemoryEntry] = []
        for path in self._iter_memory_files():
            async with aiofiles.open(path, 'r', encoding=self._encoding) as f:
                content = await f.read()
            entries.extend(self._parse_search_entries(path, content))
        return entries

    def _iter_memory_files(self) -> list[Path]:
        """Return only the current user's long-term memory file when it exists."""
        if not self._long_term_path.exists() or not self._long_term_path.is_file():
            return []
        return [self._long_term_path]

    def _parse_search_entries(self, path: Path, content: str) -> list[MemoryEntry]:
        """Parse a Markdown memory file into citation-aware searchable chunks."""
        entries: list[MemoryEntry] = []
        buffer: list[str] = []
        start_line: Optional[int] = None
        lines = content.splitlines()

        def flush(end_line: int) -> None:
            nonlocal buffer, start_line
            text = "\n".join(buffer).strip()
            if text and start_line is not None:
                timestamp = self._file_timestamp(path)
                display_path = self.display_path(path)
                entries.append(
                    MemoryEntry(
                        id=MemoryEntry.generate_id(
                            f"{display_path}:{start_line}:{text}",
                            timestamp,
                        ),
                        content=text,
                        memory_type=MemoryType.LONG_TERM,
                        timestamp=timestamp,
                        metadata={
                            "path": display_path,
                            "start_line": start_line,
                            "end_line": end_line,
                        },
                    )
                )
            buffer = []
            start_line = None

        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if self._is_memory_delimiter(stripped):
                flush(line_number - 1)
                continue
            if self._is_non_content_memory_line(stripped):
                flush(line_number - 1)
                continue
            if start_line is None:
                start_line = line_number
            buffer.append(line)

        flush(len(lines))
        return entries

    def _is_memory_delimiter(self, line: str) -> bool:
        """Return whether a Markdown line separates memory entries."""
        return not line or line == "---"

    def _is_non_content_memory_line(self, line: str) -> bool:
        """Return whether a Markdown line is structural metadata, not memory text."""
        if line.startswith("#"):
            return True
        if line.startswith("*") and line.endswith("*"):
            return True
        return any(line.startswith(prefix) for prefix in _HOOK_MEMORY_METADATA_PREFIXES)

    def _file_timestamp(self, path: Path) -> datetime:
        """Return a timezone-aware timestamp for file-backed search entries."""
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            return datetime.now(timezone.utc)

    def display_path(self, path: Path) -> str:
        """Return a stable workspace-relative path when possible."""
        resolved = path.resolve()
        try:
            return resolved.relative_to(self._workspace.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    def _resolve_memory_file(self, path: str) -> Path:
        """Resolve a user-supplied memory path and keep it under this user's memory root."""
        raw_path = Path(str(path or "").strip())
        if not str(raw_path):
            raise ValueError("Memory path is required")

        if raw_path.is_absolute():
            candidates = [raw_path]
        else:
            candidates = [
                self._memory_dir / raw_path,
                self._workspace / raw_path,
            ]

        memory_root = self._memory_dir.resolve()
        resolved_candidates = [candidate.resolve() for candidate in candidates]
        target = next(
            (candidate for candidate in resolved_candidates if candidate.exists()),
            resolved_candidates[0],
        )
        long_term_path = self._long_term_path.resolve()
        try:
            target.relative_to(memory_root)
        except ValueError as exc:
            raise ValueError("Memory path is outside the current user's memory directory") from exc
        if target != long_term_path:
            raise ValueError("Only the current user's long-term MEMORY.md may be read")
        return target
