# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.atlasclaw.skills.md_tool_runtime import ScriptInvocationConfig, create_script_wrapper


def test_script_wrapper_serializes_positional_and_flag_arguments(tmp_path: Path) -> None:
    script = tmp_path / "echo_args.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "print(json.dumps({'argv': sys.argv[1:]}))",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(
        script,
        invocation_config=ScriptInvocationConfig(
            positional_args=("identifier",),
        ),
    )
    result = asyncio.run(wrapper(identifier="TIC20260316000001", days=90))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload["argv"] == ["TIC20260316000001", "--days", "90"]


def test_script_wrapper_splits_repeatable_positional_arguments(tmp_path: Path) -> None:
    script = tmp_path / "echo_args.py"
    script.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "print(json.dumps({'argv': sys.argv[1:], 'env_ids': os.environ.get('IDS', '')}))",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(
        script,
        invocation_config=ScriptInvocationConfig(
            positional_args=("ids",),
            split_args=("ids",),
        ),
    )
    result = asyncio.run(wrapper(ids="id1 id2", reason="Approved"))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload["argv"] == ["id1", "id2", "--reason", "Approved"]
    assert payload["env_ids"] == "id1 id2"


def test_script_wrapper_uses_flag_overrides_when_present(tmp_path: Path) -> None:
    script = tmp_path / "echo_args.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "print(json.dumps({'argv': sys.argv[1:]}))",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(
        script,
        invocation_config=ScriptInvocationConfig(
            flag_name_overrides={"business_group_id": "--bg-id"},
        ),
    )
    result = asyncio.run(wrapper(business_group_id="bg-123"))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload["argv"] == ["--bg-id", "bg-123"]
