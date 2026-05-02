# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.core.security_guard import encode_if_untrusted, ensure_user_work_dir
from app.atlasclaw.memory.manager import MemoryManager
from app.atlasclaw.session.context import TranscriptEntry
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.tools.filesystem.read_tool import read_tool


def _build_ctx(tmp_path, user_id: str = "u-sec"):
    manager = SessionManager(workspace_path=str(tmp_path), user_id=user_id)
    deps = SkillDeps(
        user_info=UserInfo(user_id=user_id),
        session_manager=manager,
    )
    return SimpleNamespace(deps=deps)


def test_encode_if_untrusted_for_command_and_script():
    encoded_cmd, changed_cmd = encode_if_untrusted("rm -rf /tmp/test")
    assert changed_cmd is True
    assert encoded_cmd.startswith("[encoded_input:base64:")

    encoded_js, changed_js = encode_if_untrusted("<script>alert(1)</script>")
    assert changed_js is True
    assert encoded_js.startswith("[encoded_input:base64:")

    plain, changed_plain = encode_if_untrusted("请帮我总结这段内容")
    assert changed_plain is False
    assert plain == "请帮我总结这段内容"


@pytest.mark.asyncio
async def test_read_tool_is_restricted_to_user_work_dir(tmp_path):
    ctx = _build_ctx(tmp_path)
    work_dir = ensure_user_work_dir(tmp_path, "u-sec")
    (work_dir / "note.txt").write_text("hello", encoding="utf-8")

    read_ok = await read_tool(ctx, "note.txt")
    assert read_ok["is_error"] is False

    read_blocked = await read_tool(ctx, str(tmp_path / "outside.txt"))
    assert read_blocked["is_error"] is True


@pytest.mark.asyncio
async def test_read_tool_allows_registered_skill_paths(tmp_path):
    ctx = _build_ctx(tmp_path)
    skill_root = tmp_path / "external-provider" / "skills" / "approval"
    skill_root.mkdir(parents=True, exist_ok=True)
    skill_file = skill_root / "SKILL.md"
    skill_file.write_text("# approval\n", encoding="utf-8")
    ctx.deps.extra["md_skills_snapshot"] = [
        {
            "name": "approval",
            "qualified_name": "smartcmp:approval",
            "file_path": str(skill_file),
        }
    ]

    read_ok = await read_tool(ctx, str(skill_file))
    assert read_ok["is_error"] is False
    assert "# approval" in str(read_ok["content"])


@pytest.mark.asyncio
async def test_session_and_memory_store_encoded_untrusted_input(tmp_path):
    session_manager = SessionManager(workspace_path=str(tmp_path), user_id="u-sec")
    memory_manager = MemoryManager(workspace=str(tmp_path), user_id="u-sec")

    session_key = "agent:main:user:u-sec:main"
    await session_manager.append_transcript(
        session_key,
        TranscriptEntry(role="user", content="rm -rf /tmp/foo"),
    )
    transcript = await session_manager.load_transcript(session_key)
    assert transcript[0].content.startswith("[encoded_input:base64:")
    assert transcript[0].metadata.get("encoded_input") is True

    daily = await memory_manager.write_daily("console.log('x')")
    assert daily.content.startswith("[encoded_input:base64:")
    assert "encoded_input" in daily.tags

    long_term = await memory_manager.write_long_term("javascript:alert(1)")
    assert long_term.content.startswith("[encoded_input:base64:")
    assert "encoded_input" in long_term.tags
