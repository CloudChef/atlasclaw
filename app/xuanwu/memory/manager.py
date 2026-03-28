"""Markdown-backed memory persistence for Xuanwu.

The memory manager stores daily memories and long-term memories in Markdown
files under the workspace. It also provides read, search, and parsing helpers
for those files.

Storage layout::

    memory/<user_id>/YYYY-MM-DD.md   # daily memories
    memory/<user_id>/MEMORY.md       # long-term memory

Legacy flat layout (memory/YYYY-MM-DD.md) is migrated to memory/default/ on
first access.
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

from app.xuanwu.core.security_guard import encode_if_untrusted

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

    - `memory/<user_id>/YYYY-MM-DD.md` for daily memories
    - `memory/<user_id>/MEMORY.md` for long-term memory

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
        self._memory_dir = self._workspace / memory_dir / user_id
        self._long_term_path = self._workspace / memory_dir / user_id / long_term_file
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
        """Migrate legacy flat memory layout to memory/default/ sub-directory."""
        # Legacy: workspace/memory/YYYY-MM-DD.md (daily files directly in memory/)
        # New:    workspace/memory/default/YYYY-MM-DD.md
        legacy_dir = self._base_memory_dir
        default_dir = legacy_dir / "default"
        
        if not legacy_dir.exists():
            return
        
        # Detect legacy layout: any .md files directly in memory/
        legacy_md_files = list(legacy_dir.glob("*.md"))
        if legacy_md_files and not default_dir.exists():
            default_dir.mkdir(parents=True, exist_ok=True)
            for md_file in legacy_md_files:
                import shutil
                shutil.move(str(md_file), str(default_dir / md_file.name))
        
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
            if not section or section.startswith("# "):
                continue
                
            # andcontent
            lines = section.split("\n")
            timestamp = datetime.now(timezone.utc)
            entry_content = ""
            source = ""
            tags: list[str] = []
            
            for i, line in enumerate(lines):
                # timestamp
                if line.startswith("## "):
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
