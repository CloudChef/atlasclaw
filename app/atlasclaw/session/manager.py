"""Session persistence and transcript storage.

This module stores session metadata in JSON and transcripts in JSONL files.

Storage layout (new workspace-based):
```text
<workspace>/users/<user_id>/sessions/
├── sessions.json                        # Session metadata keyed by session key
├── <session_id>.jsonl                   # Main transcript file
├── <session_id>-topic-<thread_id>.jsonl # Thread-specific transcript file
└── archive/                             # Archived transcripts
```

Legacy layout (<legacy agents dir>/<agent_id>/sessions/<user_id>/) is still supported
for backward compatibility.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import aiofiles
import aiofiles.os

from app.atlasclaw.core.security_guard import encode_if_untrusted
from app.atlasclaw.session.context import (
    SessionKey,
    SessionMetadata,
    TranscriptEntry,
    SessionScope,
)
from app.atlasclaw.core.config_schema import ResetMode


@dataclass
class TranscriptCacheEntry:
    """Cached transcript payload keyed by session key."""

    mtime_ns: int
    size_bytes: int
    entries: list[TranscriptEntry]


class SessionManager:
    """Manage session metadata and transcript files.

    The manager provides CRUD-style operations for session state, transcript
    loading and persistence, and automatic reset or archival policies.

    Example:
        ```python
manager = SessionManager(agents_dir="/path/to/legacy-agents")

        session = await manager.get_or_create(session_key)
        transcript = await manager.load_transcript(session_key)
        await manager.append_transcript(session_key, entry)
        await manager.reset_session(session_key)
        ```
    """
    
    METADATA_FILE = "sessions.json"
    ARCHIVE_DIR = "archive"
    
    def __init__(
        self,
        workspace_path: str = ".",
        user_id: str = "default",
        reset_mode: ResetMode = ResetMode.DAILY,
        daily_reset_hour: int = 4,
        idle_reset_minutes: int = 60,
        # Legacy parameters for backward compatibility
        agents_dir: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        """Initialize the session manager.

        Args:
            workspace_path: Path to the workspace root directory.
            user_id: User identifier for per-user storage isolation.
            reset_mode: Automatic reset policy.
            daily_reset_hour: Hour of day used by daily reset mode.
            idle_reset_minutes: Idle timeout used by idle reset mode.
            agents_dir: (Legacy) Root directory that stores agent data.
            agent_id: (Legacy) Agent identifier.
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.user_id = user_id
        self.reset_mode = reset_mode
        self.daily_reset_hour = daily_reset_hour
        self.idle_reset_minutes = idle_reset_minutes
        
        # Session storage root: users/<user_id>/sessions/
        self.sessions_dir = self.workspace_path / "users" / user_id / "sessions"

        # Legacy support
        self._legacy_mode = agents_dir is not None
        if self._legacy_mode:
            self.agents_dir = Path(agents_dir).expanduser()
            self.agent_id = agent_id or "main"
            self.sessions_dir = self.agents_dir / self.agent_id / "sessions" / user_id

        # In-memory metadata cache and per-session locks.
        self._metadata_cache: dict[str, SessionMetadata] = {}
        self._transcript_cache: dict[str, TranscriptCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._io_retry_attempts = 3
        self._io_retry_backoff_seconds = 0.05
        self._archive_budget_bytes = 200 * 1024 * 1024
        self._loaded = False
    
    async def _ensure_dir(self) -> None:
        """Ensure the session and archive directories exist, migrating legacy data if needed."""
        await self._migrate_legacy_sessions()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        (self.sessions_dir / self.ARCHIVE_DIR).mkdir(exist_ok=True)
    
    async def _migrate_legacy_sessions(self) -> None:
        """Migrate legacy session data (no user_id sub-dir) to sessions/default/."""
        # Only run migration in legacy mode
        if not self._legacy_mode:
            return
        # Legacy layout: agents_dir/agent_id/sessions/sessions.json (flat)
        # New layout:    agents_dir/agent_id/sessions/default/sessions.json
        legacy_base = self.agents_dir / self.agent_id / "sessions"
        default_dir = legacy_base / "default"
        legacy_metadata = legacy_base / self.METADATA_FILE
        
        if legacy_metadata.exists() and not (default_dir / self.METADATA_FILE).exists():
            default_dir.mkdir(parents=True, exist_ok=True)
            (default_dir / self.ARCHIVE_DIR).mkdir(exist_ok=True)
            shutil.move(str(legacy_metadata), str(default_dir / self.METADATA_FILE))
            # Move all JSONL transcript files
            for jsonl_file in legacy_base.glob("*.jsonl"):
                shutil.move(str(jsonl_file), str(default_dir / jsonl_file.name))
            # Move archived transcripts
            legacy_archive = legacy_base / self.ARCHIVE_DIR
            if legacy_archive.exists():
                for archived in legacy_archive.glob("*.jsonl"):
                    shutil.move(str(archived), str(default_dir / self.ARCHIVE_DIR / archived.name))
    
    async def _get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the lock associated with a session key."""
        if session_key not in self._locks:
            self._locks[session_key] = asyncio.Lock()
        return self._locks[session_key]

    @staticmethod
    def _clone_entries(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
        """Return a deep-ish clone of transcript entries for cache safety."""
        return [TranscriptEntry.from_dict(entry.to_dict()) for entry in entries]

    def _invalidate_transcript_cache(self, session_key: str) -> None:
        """Invalidate cached transcript entries for a session key."""
        self._transcript_cache.pop(session_key, None)

    async def _read_text_with_retry(self, path: Path, *, mode: str = "r") -> str:
        """Read a UTF-8 text file with bounded retry for transient I/O failures."""
        max_attempts = max(1, int(self._io_retry_attempts))
        for attempt in range(max_attempts):
            try:
                async with aiofiles.open(path, mode, encoding="utf-8") as f:
                    return await f.read()
            except Exception:
                if attempt + 1 >= max_attempts:
                    raise
                await asyncio.sleep(self._io_retry_backoff_seconds * (attempt + 1))
        return ""

    async def _read_transcript_entries_with_retry(self, transcript_path: Path) -> list[TranscriptEntry]:
        """Read transcript JSONL content with retry and parse into entries."""
        max_attempts = max(1, int(self._io_retry_attempts))
        for attempt in range(max_attempts):
            try:
                entries: list[TranscriptEntry] = []
                async with aiofiles.open(transcript_path, "r", encoding="utf-8") as f:
                    async for line in f:
                        normalized = line.strip()
                        if not normalized:
                            continue
                        data = json.loads(normalized)
                        entries.append(TranscriptEntry.from_dict(data))
                return entries
            except Exception:
                if attempt + 1 >= max_attempts:
                    raise
                await asyncio.sleep(self._io_retry_backoff_seconds * (attempt + 1))
        return []

    @staticmethod
    def _is_retryable_replace_error(exc: BaseException) -> bool:
        """Return whether metadata atomic-replace failure is transient and retryable."""
        if not isinstance(exc, OSError):
            return False
        winerror = int(getattr(exc, "winerror", 0) or 0)
        errno = int(getattr(exc, "errno", 0) or 0)
        return winerror in {32, 33} or errno in {13}

    async def _replace_file_with_retry(self, src: Path, dst: Path) -> None:
        """Atomically replace a file with bounded retry to tolerate transient file locks."""
        max_attempts = max(1, int(self._io_retry_attempts))
        for attempt in range(max_attempts):
            try:
                await aiofiles.os.replace(src, dst)
                return
            except Exception as exc:
                if not self._is_retryable_replace_error(exc) or attempt + 1 >= max_attempts:
                    raise
                await asyncio.sleep(self._io_retry_backoff_seconds * (attempt + 1))

    async def _enforce_archive_budget(self) -> None:
        """Ensure archived transcript size stays within configured byte budget."""
        budget = int(self._archive_budget_bytes or 0)
        if budget <= 0:
            return

        archive_dir = self.sessions_dir / self.ARCHIVE_DIR
        if not archive_dir.exists():
            return

        archive_files = [path for path in archive_dir.glob("*.jsonl") if path.is_file()]
        if not archive_files:
            return

        total_bytes = sum(path.stat().st_size for path in archive_files)
        if total_bytes <= budget:
            return

        archive_files.sort(key=lambda item: item.stat().st_mtime)
        for file_path in archive_files:
            if total_bytes <= budget:
                break
            try:
                file_size = file_path.stat().st_size
                file_path.unlink()
                total_bytes -= file_size
            except Exception:
                continue
    
    async def _load_metadata(self) -> None:
        """Load session metadata from disk into the in-memory cache."""
        if self._loaded:
            return
        
        await self._ensure_dir()
        metadata_path = self.sessions_dir / self.METADATA_FILE
        
        if metadata_path.exists():
            try:
                content = await self._read_text_with_retry(metadata_path)
                if not content.strip():
                    self._loaded = True
                    return
                data = json.loads(content)
                for key, value in data.items():
                    self._metadata_cache[key] = SessionMetadata.from_dict(value)
            except Exception as e:
                print(f"[SessionManager] Failed to load metadata: {e}")
        
        self._loaded = True
    
    async def _save_metadata(self) -> None:
        """Persist the in-memory metadata cache to disk."""
        await self._ensure_dir()
        metadata_path = self.sessions_dir / self.METADATA_FILE
        tmp_path = self._build_metadata_tmp_path(metadata_path)
        
        data = {key: meta.to_dict() for key, meta in self._metadata_cache.items()}

        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        await self._replace_file_with_retry(tmp_path, metadata_path)

    @staticmethod
    def _build_metadata_tmp_path(metadata_path: Path) -> Path:
        """Return a per-save temporary path to avoid concurrent replace collisions."""
        unique_suffix = uuid.uuid4().hex
        return metadata_path.with_name(f"{metadata_path.name}.{unique_suffix}.tmp")
    
    def _get_transcript_path(self, session: SessionMetadata) -> Path:
        """Return the transcript file path for a session."""
        session_key = SessionKey.from_string(session.session_key)
        if session_key.thread_id:
            filename = f"{session.session_id}-topic-{session_key.thread_id}.jsonl"
        else:
            filename = f"{session.session_id}.jsonl"
        return self.sessions_dir / filename
    
    def _should_reset(self, session: SessionMetadata) -> bool:
        """Return whether the session should be reset automatically.

        Reset behavior depends on the configured policy:
        - `DAILY`: reset after the configured daily reset hour
        - `IDLE`: reset after the idle timeout expires
        - `MANUAL`: never reset automatically

        Args:
            session: Session metadata to evaluate.
        """
        now = datetime.now()
        
        if self.reset_mode == ResetMode.MANUAL:
            return False
        
        if self.reset_mode == ResetMode.DAILY:
            # Compute the most recent reset cutoff.
            today_reset = now.replace(
                hour=self.daily_reset_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if now.hour < self.daily_reset_hour:
                # Before the reset hour, use the previous day as the cutoff.
                today_reset -= timedelta(days=1)

            # Reset when the last update predates the cutoff.
            return session.updated_at < today_reset
        
        if self.reset_mode == ResetMode.IDLE:
            # Reset when the session has been idle past the timeout.
            idle_threshold = now - timedelta(minutes=self.idle_reset_minutes)
            return session.updated_at < idle_threshold
        
        return False
    
    async def get_or_create(self, session_key: str) -> SessionMetadata:
        """Return an existing session or create a new one.

        Args:
            session_key: Serialized session key.

        Returns:
            The active session metadata.
        """
        await self._load_metadata()
        lock = await self._get_lock(session_key)
        
        async with lock:
            if session_key in self._metadata_cache:
                session = self._metadata_cache[session_key]
                
                # Replace the session when the reset policy requires it.
                if self._should_reset(session):
                    await self._archive_session(session)
                    session = self._create_new_session(session_key)
                else:
                    # Touch the session on access.
                    session.updated_at = datetime.now()
            else:
                session = self._create_new_session(session_key)
            
            self._metadata_cache[session_key] = session
            await self._save_metadata()
            return session
    
    def _create_new_session(self, session_key: str) -> SessionMetadata:
        """Create a new session"""
        key = SessionKey.from_string(session_key)
        return SessionMetadata(
            session_key=session_key,
            agent_id=key.agent_id,
            channel=key.channel,
            account_id=key.account_id,
            peer_id=key.peer_id,
        )
    
    async def _archive_session(self, session: SessionMetadata) -> None:
        """Move the current transcript file into the archive directory."""
        transcript_path = self._get_transcript_path(session)
        if transcript_path.exists():
            archive_dir = self.sessions_dir / self.ARCHIVE_DIR
            archive_name = f"{session.session_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}.jsonl"
            shutil.move(str(transcript_path), str(archive_dir / archive_name))
            self._invalidate_transcript_cache(session.session_key)
            await self._enforce_archive_budget()
    
    async def load_transcript(self, session_key: str) -> list[TranscriptEntry]:
        """Load the transcript entries for a session.

        Args:
            session_key: Serialized session key.

        Returns:
            Transcript entries in stored order.
        """
        await self._load_metadata()
        
        if session_key not in self._metadata_cache:
            return []
        
        session = self._metadata_cache[session_key]
        transcript_path = self._get_transcript_path(session)
        
        if not transcript_path.exists():
            self._invalidate_transcript_cache(session_key)
            return []

        try:
            stat_result = transcript_path.stat()
            cache_entry = self._transcript_cache.get(session_key)
            if (
                cache_entry is not None
                and cache_entry.mtime_ns == stat_result.st_mtime_ns
                and cache_entry.size_bytes == stat_result.st_size
            ):
                return self._clone_entries(cache_entry.entries)

            entries = await self._read_transcript_entries_with_retry(transcript_path)
            self._transcript_cache[session_key] = TranscriptCacheEntry(
                mtime_ns=stat_result.st_mtime_ns,
                size_bytes=stat_result.st_size,
                entries=self._clone_entries(entries),
            )
            return entries
        except Exception as e:
            print(f"[SessionManager] Failed to load transcript: {e}")
            return []
    
    async def append_transcript(
        self,
        session_key: str,
        entry: TranscriptEntry,
    ) -> None:
        """Append a single transcript entry to the session transcript.

        Args:
            session_key: Serialized session key.
            entry: Transcript entry to append.
        """
        session = await self.get_or_create(session_key)
        transcript_path = self._get_transcript_path(session)
        
        content = entry.content
        metadata = dict(entry.metadata)
        if str(entry.role).lower() == "user":
            content, encoded = encode_if_untrusted(content)
            if encoded:
                metadata["encoded_input"] = True
        sanitized = TranscriptEntry(
            timestamp=entry.timestamp,
            role=entry.role,
            content=content,
            tool_name=getattr(entry, "tool_name", ""),
            tool_call_id=getattr(entry, "tool_call_id", ""),
            tool_calls=entry.tool_calls,
            tool_results=entry.tool_results,
            metadata=metadata,
        )
        async with aiofiles.open(transcript_path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(sanitized.to_dict(), ensure_ascii=False) + "\n")
        self._invalidate_transcript_cache(session_key)
        
        # Update the session timestamp after appending.
        session.updated_at = datetime.now()
        await self._save_metadata()
    
    async def persist_transcript(
        self,
        session_key: str,
        messages: list[dict],
    ) -> None:
        """Rewrite the transcript from a normalized message list.

        This is primarily used by workflows that replace the full transcript,
        such as compaction or queue-based persistence.

        Args:
            session_key: Serialized session key.
            messages: Normalized message dictionaries.
        """
        session = await self.get_or_create(session_key)
        transcript_path = self._get_transcript_path(session)
        
        async with aiofiles.open(transcript_path, "w", encoding="utf-8") as f:
            for msg in messages:
                entry = TranscriptEntry(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    tool_name=msg.get("tool_name", msg.get("name", "")),
                    tool_call_id=msg.get("tool_call_id", msg.get("id", "")),
                    tool_calls=msg.get("tool_calls", []),
                    tool_results=msg.get("tool_results", []),
                    metadata=msg.get("metadata", {}) if isinstance(msg.get("metadata", {}), dict) else {},
                )
                if str(entry.role).lower() == "user":
                    entry.content, encoded = encode_if_untrusted(entry.content)
                    if encoded:
                        entry.metadata["encoded_input"] = True
                await f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        self._invalidate_transcript_cache(session_key)
        
        session.updated_at = datetime.now()
        await self._save_metadata()
    
    async def reset_session(
        self,
        session_key: str,
        archive: bool = True,
    ) -> SessionMetadata:
        """Reset a session by creating a fresh metadata record.

        Args:
            session_key: Serialized session key.
            archive: Whether to archive the previous transcript first.

        Returns:
            The new session metadata.
        """
        await self._load_metadata()
        lock = await self._get_lock(session_key)
        
        async with lock:
            if session_key in self._metadata_cache:
                old_session = self._metadata_cache[session_key]
                if archive:
                    await self._archive_session(old_session)
            
            # Create a new session
            new_session = self._create_new_session(session_key)
            self._metadata_cache[session_key] = new_session
            self._invalidate_transcript_cache(session_key)
            await self._save_metadata()
            return new_session
    
    async def delete_session(self, session_key: str) -> bool:
        """Delete a session and its transcript file.

        Args:
            session_key: Serialized session key.

        Returns:
            `True` when the session existed and was removed.
        """
        await self._load_metadata()
        lock = await self._get_lock(session_key)
        
        async with lock:
            if session_key not in self._metadata_cache:
                return False
            
            session = self._metadata_cache[session_key]
            transcript_path = self._get_transcript_path(session)
            
            # Remove the transcript file if it exists.
            if transcript_path.exists():
                transcript_path.unlink()

            # Remove the session from the metadata cache.
            del self._metadata_cache[session_key]
            self._invalidate_transcript_cache(session_key)
            await self._save_metadata()
            return True
    
    async def list_sessions(self) -> list[SessionMetadata]:
        """Return metadata for all known sessions."""
        await self._load_metadata()
        return list(self._metadata_cache.values())
    
    async def get_session(self, session_key: str) -> Optional[SessionMetadata]:
        """Return session metadata without creating a new session.

        Args:
            session_key: Serialized session key.

        Returns:
            The session metadata, or `None` if it does not exist.
        """
        await self._load_metadata()
        return self._metadata_cache.get(session_key)
    
    async def update_token_stats(
        self,
        session_key: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        context_tokens: int = 0,
    ) -> None:
        """Update token accounting fields for a session.

        Args:
            session_key: Serialized session key.
            input_tokens: Number of prompt or input tokens used.
            output_tokens: Number of completion or output tokens used.
            context_tokens: Estimated size of the active context window.
        """
        session = await self.get_or_create(session_key)
        session.input_tokens += input_tokens
        session.output_tokens += output_tokens
        session.total_tokens += input_tokens + output_tokens
        session.context_tokens = context_tokens
        session.updated_at = datetime.now()
        await self._save_metadata()

    async def update_title(
        self,
        session_key: str,
        *,
        title: str,
        title_status: str,
    ) -> None:
        """Persist session title metadata."""
        session = await self.get_or_create(session_key)
        normalized_title = (title or "").strip()
        session.title = normalized_title
        session.title_status = title_status
        session.updated_at = datetime.now()
        await self._save_metadata()
    
    async def mark_compacted(self, session_key: str) -> None:
        """Record that transcript compaction has completed for a session."""
        session = await self.get_or_create(session_key)
        session.compaction_count += 1
        session.last_compacted_at = datetime.now()
        session.memory_flushed_this_cycle = False  # Reset the flush flag after compaction.
        await self._save_metadata()
