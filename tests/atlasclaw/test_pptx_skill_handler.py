# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2].parent
    / "atlasclaw-providers"
    / "skills"
    / "pptx"
    / "scripts"
    / "handler.py"
)


def _load_module():
    scripts_dir = str(_SCRIPT_PATH.parent)
    inserted = False
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
        inserted = True
    try:
        spec = importlib.util.spec_from_file_location("pptx_skill_handler_script", _SCRIPT_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if inserted:
            sys.path.remove(scripts_dir)


class _FakeSessionManager:
    def __init__(self, *, workspace_path: Path) -> None:
        self.workspace_path = workspace_path


def _build_ctx(*, workspace_path: Path):
    session_manager = _FakeSessionManager(workspace_path=workspace_path)
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            session_manager=session_manager,
            user_info=SimpleNamespace(user_id="admin"),
        )
    )
    return ctx, session_manager


def test_create_deck_handler_accepts_string_items(tmp_path: Path) -> None:
    module = _load_module()
    ctx, _ = _build_ctx(workspace_path=tmp_path / "workspace")

    result = module.create_deck_handler(
        ctx,
        items=["工单概览", "当前共有 3 项待审批申请", "请尽快安排审批"],
        title="CMP 待审批申请汇总",
        output_filename="string-items.pptx",
    )

    assert result["success"] is True
    assert result["item_count"] == 3
    assert result["file_path"] == "string-items.pptx"
    assert result["artifact_path"] == "string-items.pptx"
    output_path = tmp_path / "workspace" / "users" / "admin" / "work_dir" / "string-items.pptx"
    assert output_path.is_file()
