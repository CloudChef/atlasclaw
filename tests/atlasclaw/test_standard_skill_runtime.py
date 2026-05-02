# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.atlasclaw.agent.runner_prompt_context import collect_tools_snapshot
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import (
    inject_standard_skill_runtime_tools,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools
from app.atlasclaw.tools.skill_runtime_tools import (
    _runtime_dirs,
    _runtime_env,
    skill_exec_tool,
    skill_process_tool,
    skill_write_tool,
)


def test_standard_skill_runtime_tools_are_registered_but_hidden() -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)

    assert registry.get("skill_exec") is not None
    assert registry.get("skill_write") is not None
    assert "skill_exec" not in {item["name"] for item in registry.tools_snapshot()}
    assert "skill_exec" not in {item["name"] for item in registry.snapshot()}
    assert "skill_exec" in {
        item["name"] for item in registry.internal_runtime_tools_snapshot()
    }


def test_collect_tools_snapshot_hides_standard_runtime_tools_without_flag() -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)
    agent = SimpleNamespace()
    registry.register_to_agent(agent)

    hidden = {item["name"] for item in collect_tools_snapshot(agent=agent)}
    internal_tools = registry.internal_runtime_tools_snapshot()
    visible = {
        item["name"]
        for item in collect_tools_snapshot(
            agent=agent,
            deps=SimpleNamespace(
                extra={
                    "standard_skill_runtime_tools_visible": True,
                    "tools_snapshot": internal_tools,
                    "tools_snapshot_authoritative": True,
                }
            ),
        )
    }

    assert "skill_exec" not in hidden
    assert "skill_exec" in visible


def test_standard_runtime_injects_only_for_docs_only_target_skill() -> None:
    runtime_tools = [
        {"name": "skill_exec", "description": "exec", "source": "internal_runtime"},
        {"name": "skill_write", "description": "write", "source": "internal_runtime"},
    ]
    deps = SkillDeps(
        extra={
            "internal_runtime_tools_snapshot": runtime_tools,
            "md_skills_snapshot": [
                {
                    "name": "xlsx",
                    "qualified_name": "xlsx",
                    "metadata": {},
                }
            ],
        }
    )

    available, trace, target = inject_standard_skill_runtime_tools(
        available_tools=[],
        deps=deps,
        target_md_skill={
            "qualified_name": "xlsx",
            "file_path": "/tmp/skills/xlsx/SKILL.md",
        },
    )

    assert trace["enabled"] is True
    assert {item["name"] for item in available} == {"skill_exec", "skill_write"}
    assert deps.extra["standard_skill_runtime_enabled"] is True
    assert target and target["standard_runtime_enabled"] is True


def test_standard_runtime_does_not_inject_when_skill_declares_tool() -> None:
    deps = SkillDeps(
        extra={
            "internal_runtime_tools_snapshot": [
                {"name": "skill_exec", "description": "exec", "source": "internal_runtime"},
            ],
            "md_skills_snapshot": [
                {
                    "name": "pptx",
                    "qualified_name": "pptx",
                    "metadata": {
                        "tool_create_name": "pptx_create_deck",
                        "tool_create_entrypoint": "scripts/handler.py:create_deck_handler",
                    },
                }
            ],
        }
    )

    available, trace, target = inject_standard_skill_runtime_tools(
        available_tools=[{"name": "pptx_create_deck"}],
        deps=deps,
        target_md_skill={
            "qualified_name": "pptx",
            "file_path": "/tmp/skills/pptx/SKILL.md",
        },
    )

    assert trace["enabled"] is False
    assert trace["reason"] == "target_skill_has_executable_tool"
    assert available == [{"name": "pptx_create_deck"}]
    assert target and "standard_runtime_enabled" not in target


def test_standard_runtime_does_not_inject_for_provider_bound_docs_only_skill() -> None:
    deps = SkillDeps(
        extra={
            "internal_runtime_tools_snapshot": [
                {"name": "skill_exec", "description": "exec", "source": "internal_runtime"},
            ],
            "md_skills_snapshot": [
                {
                    "name": "export",
                    "qualified_name": "acme:export",
                    "provider": "acme",
                    "metadata": {"provider_type": "acme"},
                }
            ],
        }
    )

    available, trace, target = inject_standard_skill_runtime_tools(
        available_tools=[],
        deps=deps,
        target_md_skill={
            "provider": "acme",
            "qualified_name": "acme:export",
            "file_path": "/tmp/skills/acme/export/SKILL.md",
        },
    )

    assert trace["enabled"] is False
    assert trace["reason"] == "target_skill_provider_bound"
    assert available == []
    assert target and "standard_runtime_enabled" not in target


def test_skill_exec_uses_work_dir_scoped_home_and_tmp(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    command = (
        "python -c \"import json, os, pathlib; "
        "pathlib.Path('env.json').write_text(json.dumps({"
        "'work': os.environ['ATLASCLAW_WORK_DIR'], "
        "'skill': os.environ['ATLASCLAW_SKILL_DIR'], "
        "'home': os.environ['HOME'], "
        "'tmp': os.environ['TMPDIR'], "
        "'config': os.environ['XDG_CONFIG_HOME']}))\""
    )
    result = asyncio.run(skill_exec_tool(ctx, command=command))

    assert result["is_error"] is False
    env_file = workspace / "users" / "u1" / "work_dir" / "env.json"
    payload = json.loads(env_file.read_text(encoding="utf-8"))
    assert payload["work"] == str(workspace / "users" / "u1" / "work_dir")
    assert payload["skill"] == str(skill_dir)
    assert payload["home"].startswith(payload["work"])
    assert payload["tmp"].startswith(payload["work"])
    assert payload["config"].startswith(payload["work"])


def test_standard_runtime_env_does_not_inherit_server_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ATLASCLAW_SECRET_FOR_TEST", "must-not-leak")
    monkeypatch.setenv("PATH", "/bin:/usr/bin")

    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    env = _runtime_env(ctx)

    assert env["PATH"] == "/bin:/usr/bin"
    assert env["ATLASCLAW_WORK_DIR"] == str(workspace / "users" / "u1" / "work_dir")
    assert "ATLASCLAW_SECRET_FOR_TEST" not in env


def test_skill_exec_returns_explicit_download_paths_for_generated_files(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=(
                "python -c \"from pathlib import Path; "
                "Path('report.pdf').write_bytes(b'%PDF-1.4\\\\n'); "
                "Path('tmp.log').write_text('debug')\""
            ),
            download_paths=["report.pdf"],
        )
    )

    assert result["is_error"] is False
    assert result["details"]["download_path"] == ["report.pdf"]


def test_skill_exec_does_not_infer_download_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=(
                "python -c \"from pathlib import Path; "
                "Path('report.pdf').write_bytes(b'%PDF-1.4\\\\n')\""
            ),
        )
    )

    assert result["is_error"] is False
    assert "download_path" not in result["details"]
    assert "No download_paths were provided" in result["content"][0]["text"]


def test_skill_exec_rejects_hidden_runtime_download_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=(
                "python -c \"from pathlib import Path; "
                "Path('.atlasclaw/skills/skill-pdf/cache').mkdir(parents=True, exist_ok=True); "
                "Path('.atlasclaw/skills/skill-pdf/cache/debug.pdf').write_bytes(b'debug')\""
            ),
            download_paths=[".atlasclaw/skills/skill-pdf/cache/debug.pdf"],
        )
    )

    assert result["is_error"] is True
    assert "download_paths must reference visible files" in result["content"][0]["text"]


def test_skill_exec_rejects_missing_hidden_download_path(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command="python -c \"print('done')\"",
            download_paths=[".atlasclaw/skills/skill-pdf/cache/missing.pdf"],
        )
    )

    assert result["is_error"] is True
    assert "download_paths must reference visible files" in result["content"][0]["text"]


def test_skill_exec_rejects_home_relative_command_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=(
                "python -c \"from pathlib import Path; "
                "Path('~/bad.pdf').expanduser().write_text('bad')\""
            ),
        )
    )

    assert result["is_error"] is True
    assert "~" in result["content"][0]["text"]


def test_skill_exec_rejects_home_relative_user_requested_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    deps.user_message = "Generate a PDF at ~/bad.pdf"
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command="python -c \"print('would create file')\"",
        )
    )

    assert result["is_error"] is True
    assert "home-relative output paths are not allowed" in result["content"][0]["text"]


def test_skill_exec_rejects_absolute_command_paths_outside_work_dir(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.pdf"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=(
                "python -c \"from pathlib import Path; "
                f"Path({str(outside)!r}).write_text('bad')\""
            ),
        )
    )

    assert result["is_error"] is True
    assert "inside work_dir" in result["content"][0]["text"]
    assert not outside.exists()


def test_standard_runtime_tools_reject_direct_invocation_without_selected_skill(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_write_tool(ctx, file_path="leak.txt", content="should not write")
    )

    assert result["is_error"] is True
    assert not (workspace / "users" / "u1" / "work_dir" / "leak.txt").exists()


def test_standard_runtime_processes_are_scoped_to_user_session_and_skill(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    deps_one = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_key="s1",
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    deps_two = SkillDeps(
        user_info=UserInfo(user_id="u2", display_name="User Two"),
        session_key="s1",
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    ctx_one = SimpleNamespace(deps=deps_one)
    ctx_two = SimpleNamespace(deps=deps_two)

    async def run_case() -> None:
        start = await skill_process_tool(
            ctx_one,
            action="start",
            command="python -u -c \"import time; print('ready'); time.sleep(30)\"",
        )
        process_id = start["details"]["process_id"]
        blocked = await skill_process_tool(ctx_two, action="poll", process_id=process_id)
        cleanup = await skill_process_tool(ctx_one, action="kill", process_id=process_id)

        assert start["is_error"] is False
        assert blocked["is_error"] is True
        assert cleanup["is_error"] is False

    asyncio.run(run_case())


def test_standard_runtime_skill_directories_do_not_collapse_similar_skill_ids(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")

    def ctx_for(qualified_name: str) -> SimpleNamespace:
        return SimpleNamespace(
            deps=SkillDeps(
                user_info=UserInfo(user_id="u1", display_name="User One"),
                session_manager=SimpleNamespace(workspace_path=workspace),
                extra={
                    "standard_skill_runtime_enabled": True,
                    "target_md_skill": {
                        "qualified_name": qualified_name,
                        "file_path": str(skill_file),
                    },
                },
            )
        )

    first_root = _runtime_dirs(ctx_for("a:b"))["root"]
    second_root = _runtime_dirs(ctx_for("a_b"))["root"]

    assert first_root != second_root
