# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Markdown-backed memory persistence for AtlasClaw.

The memory manager stores daily memories and long-term memories in Markdown
files under the workspace. It also provides read, search, and parsing helpers
for those files.

Storage layout::

    users/<user_id>/memory/YYYY-MM-DD.md   # daily memories
    users/<user_id>/memory/MEMORY.md       # long-term memory

Legacy layouts under memory/ are migrated to users/<user_id>/memory/ on first
access.
"""

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import aiofiles

from app.atlasclaw.core.security_guard import encode_if_untrusted
from app.atlasclaw.core.user_paths import normalize_runtime_user_id, user_runtime_dir

_HOOK_MEMORY_METADATA_PREFIXES = (
    "- timestamp_utc:",
    "- module_name:",
    "- user_id:",
    "- source_event_ids:",
)


class MemoryType(Enum):
    """Memory storage category."""
    DAILY = "daily"      # Date-scoped short-term memory
    LONG_TERM = "long_term"  # Persistent long-term memory
    EPHEMERAL = "ephemeral"  # Session-scoped transient memory


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
    memory_type: MemoryType = MemoryType.DAILY
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
    Manager for Markdown-based memory storage.

    The manager maintains:

    - `users/<user_id>/memory/YYYY-MM-DD.md` for daily memories
    - `users/<user_id>/memory/MEMORY.md` for long-term memory

    It supports writing, parsing, loading, and searching memory entries.
    """
    
    def __init__(
        self,
        workspace: str,
        *,
        memory_dir: str = "memory",
        long_term_file: str = "MEMORY.md",
        user_id: str = "default",
        daily_prefix: str = "",
        encoding: str = "utf-8",
    ) -> None:
        """
        Initialize the memory manager.

        Args:
            workspace: Workspace root path.
            memory_dir: Base directory used for memory files.
            long_term_file: File name for long-term memory storage.
            user_id: User identifier for per-user storage isolation.
            daily_prefix: Optional prefix for daily memory file names.
            encoding: File encoding used for all memory files.
        """
        self._workspace = Path(workspace)
        self._user_id = user_id
        self._storage_user_id = normalize_runtime_user_id(user_id)
        self._memory_dir = user_runtime_dir(self._workspace, user_id) / "memory"
        self._long_term_path = self._memory_dir / long_term_file
        self._daily_prefix = daily_prefix
        self._encoding = encoding
        self._base_memory_dir = self._workspace / memory_dir
        
        # In-memory cache for parsed entries.
        self._cache: dict[str, MemoryEntry] = {}
        self._cache_loaded = False
        
        # Serialize writes across concurrent tasks.
        self._write_lock = asyncio.Lock()
        
    @property
    def memory_dir(self) -> Path:
        """Return the directory used for daily memory files."""
        return self._memory_dir
        
    @property
    def long_term_path(self) -> Path:
        """Return the long-term memory file path."""
        return self._long_term_path
        
    def _get_daily_path(self, date: Optional[datetime] = None) -> Path:
        """Return the file path for a daily memory file."""
        if date is None:
            date = datetime.now(timezone.utc)
        date_str = date.strftime("%Y-%m-%d")
        filename = f"{self._daily_prefix}{date_str}.md" if self._daily_prefix else f"{date_str}.md"
        return self._memory_dir / filename
        
    async def ensure_dirs(self) -> None:
        """Ensure the memory directory exists, migrating legacy data if needed."""
        await self._migrate_legacy_memory()
        self._memory_dir.mkdir(parents=True, exist_ok=True)
    
    async def _migrate_legacy_memory(self) -> None:
        """Migrate legacy memory layouts into the documented per-user directory."""
        # Legacy: workspace/memory/YYYY-MM-DD.md (daily files directly in memory/)
        # Legacy: workspace/memory/<user_id>/*.md
        # New:    workspace/users/<user_id>/memory/*.md
        legacy_dir = self._base_memory_dir
        
        if not legacy_dir.exists():
            return
        
        # Detect legacy layout: any .md files directly in memory/
        legacy_md_files = list(legacy_dir.glob("*.md"))
        if legacy_md_files and self._storage_user_id == "default":
            self._memory_dir.mkdir(parents=True, exist_ok=True)
            for md_file in legacy_md_files:
                import shutil
                target = self._memory_dir / md_file.name
                if not target.exists():
                    shutil.move(str(md_file), str(target))

        legacy_user_dir = legacy_dir / self._storage_user_id
        if legacy_user_dir.exists() and legacy_user_dir.resolve() != self._memory_dir.resolve():
            self._memory_dir.mkdir(parents=True, exist_ok=True)
            for md_file in legacy_user_dir.glob("*.md"):
                import shutil
                target = self._memory_dir / md_file.name
                if not target.exists():
                    shutil.move(str(md_file), str(target))
        
    async def write_daily(
        self,
        content: str,
        *,
        source: str = "",
        tags: Optional[list[str]] = None,
        timestamp: Optional[datetime] = None,
    ) -> MemoryEntry:
        """
        Append a daily memory entry to the appropriate Markdown file.

        Args:
            content: Memory content.
            source: Source identifier.
            tags: Optional tag list.
            timestamp: Optional timestamp override.

        Returns:
            The created memory entry.
        """
        await self.ensure_dirs()
        
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
            
        safe_content, encoded = encode_if_untrusted(content)
        entry = MemoryEntry(
            id=MemoryEntry.generate_id(content, timestamp),
            content=safe_content,
            memory_type=MemoryType.DAILY,
            source=source,
            timestamp=timestamp,
            tags=(tags or []) + (["encoded_input"] if encoded else []),
        )
        
        # Format the entry as Markdown before writing it.
        formatted = self._format_entry(entry)
        
        # Append to the daily file, creating the header when needed.
        daily_path = self._get_daily_path(timestamp)
        async with self._write_lock:
            mode = 'a' if daily_path.exists() else 'w'
            async with aiofiles.open(daily_path, mode, encoding=self._encoding) as f:
                if mode == 'w':
                    # Write a heading when the file is created for the first time.
                    header = f"# Daily Memory - {timestamp.strftime('%Y-%m-%d')}\n\n"
                    await f.write(header)
                await f.write(formatted)
                
        # Keep the new entry in the in-memory cache.
        self._cache[entry.id] = entry
        
        return entry
        
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
            # Ensure directory exists before writing
            self._long_term_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Load the existing long-term memory file before updating it.
            existing_content = ""
            if self._long_term_path.exists():
                async with aiofiles.open(self._long_term_path, 'r', encoding=self._encoding) as f:
                    existing_content = await f.read()
                    
            # Rebuild the file content with the new entry inserted.
            updated_content = self._update_long_term_content(
                existing_content, entry, section
            )
            
            # Persist the updated long-term memory file.
            async with aiofiles.open(self._long_term_path, 'w', encoding=self._encoding) as f:
                await f.write(updated_content)
                
        # Keep the new entry in the in-memory cache.
        self._cache[entry.id] = entry
        
        return entry
        
    def _format_entry(self, entry: MemoryEntry) -> str:
        """for mat memory entry markdown"""
        lines = []
        
        # timestamp
        time_str = entry.timestamp.strftime("%H:%M:%S")
        lines.append(f"## {time_str}")
        
        # metadata
        meta_parts = []
        if entry.source:
            meta_parts.append(f"Source: {entry.source}")
        if entry.tags:
            meta_parts.append(f"Tags: {', '.join(entry.tags)}")
        if meta_parts:
            lines.append(f"*{' | '.join(meta_parts)}*")
            
        lines.append("")
        
        # content
        lines.append(entry.content)
        
        lines.append("")
        lines.append("---")
        lines.append("")
        
        return "\n".join(lines)
        
    def _update_long_term_content(
        self,
        existing: str,
        entry: MemoryEntry,
        section: str
    ) -> str:
        """memory content"""
        if not existing:
            # 
            return f"# Long-term Memory\n\n## {section}\n\n{entry.content}\n"
            
        # 
        section_pattern = rf"(## {re.escape(section)}\n)"
        match = re.search(section_pattern, existing)
        
        if match:
            # at
            insert_pos = match.end()
            # to or
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
            # 
            return existing.rstrip() + f"\n\n## {section}\n\n{entry.content}\n"
            
    async def read_daily(
        self,
        date: Optional[datetime] = None
    ) -> list[MemoryEntry]:
        """


        
        Args:
            date:(default)
            
        Returns:
            Memory entry list
        
"""
        daily_path = self._get_daily_path(date)
        
        if not daily_path.exists():
            return []
            
        async with aiofiles.open(daily_path, 'r', encoding=self._encoding) as f:
            content = await f.read()
            
        return self._parse_markdown_entries(content, MemoryType.DAILY)
        
    async def read_long_term(self) -> list[MemoryEntry]:
        """


        
        Returns:
            Memory entry list
        
"""
        if not self._long_term_path.exists():
            return []
            
        async with aiofiles.open(self._long_term_path, 'r', encoding=self._encoding) as f:
            content = await f.read()
            
        return self._parse_markdown_entries(content, MemoryType.LONG_TERM)

    async def search(self, query: str, limit: int = 10) -> list[Any]:
        """
        Search user-scoped memory files.

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
            apply_recency=False,
        )
        return [result for result in results if result.score > 0]

    async def get(
        self,
        path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Read a memory file slice restricted to the current user's memory root.

        Args:
            path: Absolute path, workspace-relative path, or memory-dir-relative
                path to a Markdown memory file.
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
        if selected:
            end_line = start_line + len(selected) - 1
        else:
            end_line = start_line

        return {
            "content": "".join(selected).rstrip("\n"),
            "path": self._display_memory_path(target_path),
            "start_line": start_line,
            "end_line": end_line,
        }

    async def _load_search_entries(self) -> list[MemoryEntry]:
        """Load searchable chunks from every Markdown file in the user memory directory."""
        entries: list[MemoryEntry] = []
        for path in self._iter_memory_files():
            async with aiofiles.open(path, 'r', encoding=self._encoding) as f:
                content = await f.read()
            entries.extend(self._parse_search_entries(path, content))
        return entries

    def _iter_memory_files(self) -> list[Path]:
        """Return Markdown memory files for the current user."""
        if not self._memory_dir.exists():
            return []
        return sorted(path for path in self._memory_dir.glob("*.md") if path.is_file())

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
                entries.append(
                    MemoryEntry(
                        id=MemoryEntry.generate_id(
                            f"{self._display_memory_path(path)}:{start_line}:{text}",
                            timestamp,
                        ),
                        content=text,
                        memory_type=self._memory_type_for_file(path),
                        timestamp=timestamp,
                        metadata={
                            "path": self._display_memory_path(path),
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

    def _memory_type_for_file(self, path: Path) -> MemoryType:
        """Infer memory type from file name."""
        if path.name == self._long_term_path.name or path.name.startswith("memory_"):
            return MemoryType.LONG_TERM
        return MemoryType.DAILY

    def _file_timestamp(self, path: Path) -> datetime:
        """Return a timezone-aware timestamp for file-backed search entries."""
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            return datetime.now(timezone.utc)

    def _display_memory_path(self, path: Path) -> str:
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
        try:
            target.relative_to(memory_root)
        except ValueError as exc:
            raise ValueError("Memory path is outside the current user's memory directory") from exc
        return target
        
    def _parse_markdown_entries(
        self,
        content: str,
        memory_type: MemoryType
    ) -> list[MemoryEntry]:
        """parse markdown memory entry"""
        entries = []
        
        # --- split
        sections = content.split("\n---\n")
        
        for section in sections:
            section = section.strip()
            if not section:
                continue
                
            # andcontent
            lines = section.split("\n")
            timestamp = datetime.now(timezone.utc)
            entry_content = ""
            source = ""
            tags: list[str] = []
            
            for i, line in enumerate(lines):
                if line.startswith("# "):
                    continue
                # timestamp
                if line.startswith("## "):
                    if memory_type == MemoryType.LONG_TERM:
                        continue
                    time_str = line[3:].strip()
                    try:
                        # parse
                        parsed_time = datetime.strptime(time_str, "%H:%M:%S")
                        timestamp = timestamp.replace(
                            hour=parsed_time.hour,
                            minute=parsed_time.minute,
                            second=parsed_time.second
                        )
                    except ValueError:
                        pass
                # metadata
                elif line.startswith("*") and line.endswith("*"):
                    meta_line = line[1:-1]
                    if "Source:" in meta_line:
                        source = meta_line.split("Source:")[1].split("|")[0].strip()
                    if "Tags:" in meta_line:
                        tags_str = meta_line.split("Tags:")[1].strip()
                        tags = [t.strip() for t in tags_str.split(",")]
                else:
                    entry_content += line + "\n"
                    
            entry_content = entry_content.strip()
            if entry_content:
                entry = MemoryEntry(
                    id=MemoryEntry.generate_id(entry_content, timestamp),
                    content=entry_content,
                    memory_type=memory_type,
                    source=source,
                    timestamp=timestamp,
                    tags=tags
                )
                entries.append(entry)
                
        return entries
        
    async def load_all(self) -> list[MemoryEntry]:
        """


        
        and 7.
        
        Returns:
            memory entry
        
"""
        all_entries: list[MemoryEntry] = []
        
        # 
        long_term = await self.read_long_term()
        all_entries.extend(long_term)
        
        # 7
        today = datetime.now(timezone.utc)
        for i in range(7):
            from datetime import timedelta
            date = today - timedelta(days=i)
            daily = await self.read_daily(date)
            all_entries.extend(daily)
            
        # 
        for entry in all_entries:
            self._cache[entry.id] = entry
        self._cache_loaded = True
        
        return all_entries
        
    async def delete_entry(self, entry_id: str) -> bool:
        """

memory entry
        
        :from in,.
        
        Args:
            entry_id:entry ID
            
        Returns:
            
        
"""
        if entry_id in self._cache:
            del self._cache[entry_id]
            return True
        return False
        
    def get_cached_entries(self) -> list[MemoryEntry]:
        """get memory entry"""
        return list(self._cache.values())
        
    async def clear_daily(self, date: Optional[datetime] = None) -> bool:
        """


        
        Args:
            date:(default)
            
        Returns:
            
        
"""
        daily_path = self._get_daily_path(date)
        
        if daily_path.exists():
            os.remove(daily_path)
            return True
        return False
