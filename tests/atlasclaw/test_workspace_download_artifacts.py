# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.atlasclaw.agent.runner_tool.runner_execution_flow_stream import (
    collect_workspace_download_references_from_tool_results,
)


def _user_area(tmp_path: Path, user_id: str, area: str) -> Path:
    root = tmp_path / "users" / user_id / area
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_collects_artifact_from_absolute_tool_file_path(tmp_path: Path) -> None:
    root = _user_area(tmp_path, "alice", "work_dir")
    artifact_path = root / "exported-file.bin"
    artifact_path.write_bytes(b"artifact")

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "artifact_export",
                "content": json.dumps({"success": True, "file_path": str(artifact_path)}),
            }
        ],
        start_index=0,
        target_tool_names=["artifact_export"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == [{"path": "exported-file.bin"}]


def test_collects_artifact_from_relative_tool_file_path(tmp_path: Path) -> None:
    root = _user_area(tmp_path, "alice", "work_dir")
    artifact_path = root / "exported-file.bin"
    artifact_path.write_bytes(b"artifact")

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "artifact_export",
                "content": {"success": True, "file_path": artifact_path.name},
            }
        ],
        start_index=0,
        target_tool_names=["artifact_export"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == [{"path": "exported-file.bin"}]


def test_collects_file_written_relative_path_from_write_tool(tmp_path: Path) -> None:
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "conversation.txt").write_text("history", encoding="utf-8")

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "write",
                "content": "File written: conversation.txt",
            }
        ],
        start_index=0,
        target_tool_names=["write"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == [{"path": "conversation.txt"}]


def test_ignores_read_tool_file_path_metadata(tmp_path: Path) -> None:
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "notes.txt").write_text("existing", encoding="utf-8")

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "read",
                "content": {
                    "content": "existing",
                    "details": {"file_path": "notes.txt"},
                    "is_error": False,
                },
            }
        ],
        start_index=0,
        target_tool_names=["read"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == []


def test_ignores_failed_write_file_path_metadata(tmp_path: Path) -> None:
    root = _user_area(tmp_path, "alice", "work_dir")
    (root / "stale.txt").write_text("old", encoding="utf-8")

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "write",
                "content": {
                    "details": {"file_path": "stale.txt"},
                    "is_error": True,
                },
            }
        ],
        start_index=0,
        target_tool_names=["write"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == []


def test_collects_embedded_tool_results_once(tmp_path: Path) -> None:
    root = _user_area(tmp_path, "alice", "work_dir")
    report = root / "report.bin"
    report.write_bytes(b"artifact")

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "assistant",
                "tool_results": [
                    {
                        "tool_name": "artifact_export",
                        "content": {"file_path": str(report)},
                    },
                    {
                        "tool_name": "artifact_export",
                        "content": {"file_path": str(report)},
                    },
                ],
            }
        ],
        start_index=0,
        target_tool_names=["artifact_export"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == [{"path": "report.bin"}]


def test_ignores_other_user_artifact_path(tmp_path: Path) -> None:
    root = _user_area(tmp_path, "bob", "work_dir")
    artifact_path = root / "empty.bin"
    artifact_path.write_bytes(b"artifact")

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "artifact_export",
                "content": {"file_path": str(artifact_path)},
            }
        ],
        start_index=0,
        target_tool_names=["artifact_export"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == []


def test_ignores_symlink_escape_artifact_path(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink is not available on this platform")

    root = _user_area(tmp_path, "alice", "work_dir")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    linked = root / "linked.bin"
    linked.symlink_to(outside)

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "artifact_export",
                "content": {"file_path": str(linked)},
            }
        ],
        start_index=0,
        target_tool_names=["artifact_export"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == []


def test_ignores_symlinked_workspace_work_dir_root(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink is not available on this platform")

    outside_users = tmp_path / "outside-users"
    root = outside_users / "alice" / "work_dir"
    root.mkdir(parents=True)
    artifact_path = root / "outside-root.bin"
    artifact_path.write_bytes(b"artifact")
    (tmp_path / "users").symlink_to(outside_users, target_is_directory=True)

    refs = collect_workspace_download_references_from_tool_results(
        messages=[
            {
                "role": "tool",
                "tool_name": "artifact_export",
                "content": {"file_path": str(artifact_path)},
            }
        ],
        start_index=0,
        target_tool_names=["artifact_export"],
        workspace_path=tmp_path,
        user_id="alice",
    )

    assert refs == []
