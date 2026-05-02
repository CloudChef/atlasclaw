# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.models import ANONYMOUS_USER, UserInfo
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


def _build_client(tmp_path: Path, user_id: str | None = "alice") -> TestClient:
    ctx = APIContext(
        session_manager=SessionManager(workspace_path=str(tmp_path), user_id="default"),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
    )
    set_api_context(ctx)

    app = FastAPI()

    @app.middleware("http")
    async def inject_user_info(request, call_next):
        request.state.user_info = (
            UserInfo(user_id=user_id, display_name=user_id)
            if user_id is not None
            else ANONYMOUS_USER
        )
        return await call_next(request)

    app.include_router(create_router())
    return TestClient(app)


def _user_area(tmp_path: Path, user_id: str, area: str) -> Path:
    root = tmp_path / "users" / user_id / area
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_current_user_downloads_work_dir_file(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "notes.txt").write_text("hello from work dir", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": "notes.txt"},
    )

    assert response.status_code == 200
    assert response.content == b"hello from work dir"
    assert "attachment" in response.headers["content-disposition"]
    assert "notes.txt" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_current_user_downloads_nested_work_dir_file(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "downloads").mkdir()
    (root / "downloads" / "report.csv").write_text("id,value\n1,ok\n", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": "downloads/report.csv"},
    )

    assert response.status_code == 200
    assert response.text == "id,value\n1,ok\n"


def test_hidden_runtime_files_are_not_downloadable(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    hidden_file = root / ".atlasclaw" / "skills" / "skill-xlsx" / "cache" / "debug.txt"
    hidden_file.parent.mkdir(parents=True)
    hidden_file.write_text("internal", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": ".atlasclaw/skills/skill-xlsx/cache/debug.txt"},
    )

    assert response.status_code == 403


def test_hidden_runtime_files_are_not_downloadable_with_mixed_case_path(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    hidden_file = root / ".atlasclaw" / "skills" / "skill-xlsx" / "cache" / "debug.txt"
    hidden_file.parent.mkdir(parents=True)
    hidden_file.write_text("internal", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": ".AtlasClaw/skills/skill-xlsx/cache/debug.txt"},
    )

    assert response.status_code == 403


def test_current_user_absolute_work_dir_path_is_rejected(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    note = root / "notes.txt"
    note.write_text("hello", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": str(note)},
    )

    assert response.status_code == 403


def test_anonymous_request_is_rejected(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "notes.txt").write_text("secret", encoding="utf-8")

    client = _build_client(tmp_path, None)
    response = client.get(
        "/api/workspace/files/download",
        params={"path": "notes.txt"},
    )

    assert response.status_code == 401


def test_other_user_absolute_path_is_rejected(tmp_path):
    root = _user_area(tmp_path, "bob", "work_dir")
    bob_file = root / "notes.txt"
    bob_file.write_text("bob only", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": str(bob_file)},
    )

    assert response.status_code == 403


def test_traversal_escape_is_rejected(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    (root.parent / "sessions").mkdir(parents=True, exist_ok=True)
    (root.parent / "sessions" / "session.jsonl").write_text("private", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": "../sessions/session.jsonl"},
    )

    assert response.status_code == 403


def test_symlink_escape_is_rejected(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink is not available on this platform")

    root = _user_area(tmp_path, "alice", "work_dir")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "linked.txt").symlink_to(outside)

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": "linked.txt"},
    )

    assert response.status_code == 403


def test_symlinked_nested_directory_escape_is_rejected(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink is not available on this platform")

    root = _user_area(tmp_path, "alice", "work_dir")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("outside", encoding="utf-8")
    (root / "linked-dir").symlink_to(outside_dir, target_is_directory=True)

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": "linked-dir/secret.txt"},
    )

    assert response.status_code == 403


def test_symlinked_user_root_is_rejected(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink is not available on this platform")

    outside_users = tmp_path / "outside-users"
    root = outside_users / "alice" / "work_dir"
    root.mkdir(parents=True)
    (root / "notes.txt").write_text("outside root", encoding="utf-8")
    (tmp_path / "users").symlink_to(outside_users, target_is_directory=True)

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": "notes.txt"},
    )

    assert response.status_code == 403


def test_malformed_path_is_rejected(tmp_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "notes.txt").write_text("hello", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download?path=bad%00.txt",
    )

    assert response.status_code == 400


@pytest.mark.parametrize("requested_path", ["missing.txt", "nested"])
def test_missing_files_and_directories_are_rejected(tmp_path, requested_path):
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "nested").mkdir()

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": requested_path},
    )

    assert response.status_code == 404


def test_legacy_exports_root_is_not_downloadable(tmp_path):
    root = _user_area(tmp_path, "alice", "exports")
    legacy_export = root / "notes.txt"
    legacy_export.write_text("legacy export", encoding="utf-8")

    client = _build_client(tmp_path, "alice")
    response = client.get(
        "/api/workspace/files/download",
        params={"path": str(legacy_export)},
    )

    assert response.status_code == 403
