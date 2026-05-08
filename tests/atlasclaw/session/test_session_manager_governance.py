# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import aiofiles
import pytest

from app.atlasclaw.core.config_schema import ResetMode
from app.atlasclaw.session.context import (
    ChatType,
    SessionKey,
    SessionMetadata,
    SessionScope,
    TranscriptEntry,
)
from app.atlasclaw.session.manager import SessionManager


@pytest.mark.asyncio
async def test_load_transcript_uses_cache_until_mtime_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = SessionManager(
        workspace_path=str(tmp_path),
        user_id="u1",
        reset_mode=ResetMode.MANUAL,
    )
    session_key = "agent:main:user:u1:web:dm:p1"

    await manager.persist_transcript(session_key, [{"role": "user", "content": "hello"}])
    first = await manager.load_transcript(session_key)
    assert len(first) == 1

    session = manager._metadata_cache[session_key]
    transcript_path = manager._get_transcript_path(session)

    read_calls = {"count": 0}
    original_open = aiofiles.open

    def counting_open(file: str | os.PathLike[str], *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if Path(file) == transcript_path and mode == "r":
            read_calls["count"] += 1
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(aiofiles, "open", counting_open)

    second = await manager.load_transcript(session_key)
    assert len(second) == 1
    assert read_calls["count"] == 0

    with transcript_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(TranscriptEntry(role="assistant", content="next").to_dict(), ensure_ascii=False) + "\n")
    os.utime(transcript_path, None)
    time.sleep(0.01)

    third = await manager.load_transcript(session_key)
    assert len(third) == 2
    assert read_calls["count"] >= 1


@pytest.mark.asyncio
async def test_read_retries_on_transient_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = SessionManager(
        workspace_path=str(tmp_path),
        user_id="u1",
        reset_mode=ResetMode.MANUAL,
    )
    session_key = "agent:main:user:u1:web:dm:p1"
    await manager.persist_transcript(session_key, [{"role": "user", "content": "retry"}])
    session = manager._metadata_cache[session_key]
    transcript_path = manager._get_transcript_path(session)

    original_open = aiofiles.open
    failed_once = {"value": False}

    def flaky_open(file: str | os.PathLike[str], *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if Path(file) == transcript_path and mode == "r" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("transient read failure")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(aiofiles, "open", flaky_open)

    entries = await manager.load_transcript(session_key)
    assert failed_once["value"] is True
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_archive_budget_cleanup_removes_old_files(tmp_path: Path):
    manager = SessionManager(
        workspace_path=str(tmp_path),
        user_id="u1",
        reset_mode=ResetMode.MANUAL,
    )
    manager._archive_budget_bytes = 100
    await manager._ensure_dir()

    archive_dir = manager.sessions_dir / manager.ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)

    oldest = archive_dir / "oldest.jsonl"
    older = archive_dir / "older.jsonl"
    newest = archive_dir / "newest.jsonl"

    oldest.write_text("A" * 60, encoding="utf-8")
    older.write_text("B" * 50, encoding="utf-8")
    newest.write_text("C" * 40, encoding="utf-8")

    now = time.time()
    os.utime(oldest, (now - 300, now - 300))
    os.utime(older, (now - 200, now - 200))
    os.utime(newest, (now - 100, now - 100))

    await manager._enforce_archive_budget()

    remaining = [p.name for p in archive_dir.glob("*.jsonl")]
    remaining_size = sum(p.stat().st_size for p in archive_dir.glob("*.jsonl"))

    assert remaining_size <= manager._archive_budget_bytes
    assert "newest.jsonl" in remaining


def test_thread_transcript_filename_escapes_external_thread_id():
    """External thread IDs with path separators should not create nested transcript paths."""
    manager = SessionManager(
        workspace_path="workspace",
        user_id="u1",
        reset_mode=ResetMode.MANUAL,
    )
    session_key = SessionKey(
        agent_id="main",
        user_id="u1",
        channel="web",
        chat_type=ChatType.DM,
        peer_id="peer-1",
        thread_id="room/topic\\child",
    ).to_string(scope=SessionScope.PER_CHANNEL_PEER)
    session = SessionMetadata(
        session_id="session-1",
        session_key=session_key,
    )
    transcript_path = manager._get_transcript_path(session)

    assert transcript_path.parent == manager.sessions_dir
    assert transcript_path.name.endswith("-topic-room%2Ftopic%5Cchild.jsonl")
