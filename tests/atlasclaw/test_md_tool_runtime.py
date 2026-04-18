# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
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


def test_script_wrapper_maps_json_body_to_json_flag_and_serializes_dict(tmp_path: Path) -> None:
    script = tmp_path / "echo_args.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "argv = sys.argv[1:]",
                "parsed = json.loads(argv[1]) if len(argv) >= 2 and argv[0] == '--json' else None",
                "print(json.dumps({'argv': argv, 'parsed': parsed}, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(
        script,
        invocation_config=ScriptInvocationConfig(
            flag_name_overrides={"json_body": "--json"},
        ),
    )
    result = asyncio.run(
        wrapper(
            json_body={
                "catalogId": "catalog-1",
                "businessGroupId": "bg-1",
                "name": "server-room-network-issue",
            }
        )
    )

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload["argv"][0] == "--json"
    assert payload["parsed"] == {
        "catalogId": "catalog-1",
        "businessGroupId": "bg-1",
        "name": "server-room-network-issue",
    }


def test_script_wrapper_exposes_user_id_to_script_environment(tmp_path: Path) -> None:
    script = tmp_path / "echo_user.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({'user_id': os.environ.get('ATLASCLAW_USER_ID', '')}))",
            ]
        ),
        encoding="utf-8",
    )

    class _UserInfo:
        user_id = "admin"

    class _Deps:
        user_info = _UserInfo()

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(script)
    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload["user_id"] == "admin"


def test_script_wrapper_normalizes_crlf_output(tmp_path: Path) -> None:
    script = tmp_path / "echo_crlf.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "sys.stdout.write('line1\\r\\nline2\\r\\n')",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(script)
    result = asyncio.run(wrapper())

    assert result["success"] is True
    assert "\r" not in result["output"]
    assert "line1" in result["output"]
    assert "line2" in result["output"]
