# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

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


def test_script_wrapper_hides_silent_lookup_output_when_internal_metadata_exists(
    tmp_path: Path,
) -> None:
    script = tmp_path / "list_services.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "print('Found 3 published catalog(s).')",
                "sys.stderr.write('##PROVIDER_META_START##\\n')",
                "sys.stderr.write('{\"catalogs\": [{\"id\": \"catalog-1\", \"name\": \"Linux VM\"}]}\\n')",
                "sys.stderr.write('##PROVIDER_META_END##\\n')",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(
        script,
        tool_name="provider_list_services",
        result_mode="silent_ok",
        success_contract={},
    )
    result = asyncio.run(wrapper())

    assert result["success"] is True
    assert result["output"] == ""
    assert result["_internal"] == '{"catalogs": [{"id": "catalog-1", "name": "Linux VM"}]}'
    assert result["_lookup_output_hidden"] is True


def test_script_wrapper_keeps_visible_output_for_non_lookup_tools_even_with_internal_metadata(
    tmp_path: Path,
) -> None:
    script = tmp_path / "submit.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "print('Request submitted successfully.')",
                "sys.stderr.write('##PROVIDER_META_START##\\n')",
                "sys.stderr.write('{\"requestId\": \"TIC20260422000001\"}\\n')",
                "sys.stderr.write('##PROVIDER_META_END##\\n')",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(
        script,
        tool_name="provider_submit_request",
        result_mode="silent_ok",
        success_contract={"required_fields": ["requestId"]},
    )
    result = asyncio.run(wrapper())

    assert result["success"] is True
    assert result["output"] == "Request submitted successfully.\n"
    assert result["_internal"] == '{"requestId": "TIC20260422000001"}'
    assert "_lookup_output_hidden" not in result


def test_script_wrapper_logs_tool_name_and_masks_sensitive_env_values(
    tmp_path: Path,
    capsys,
) -> None:
    script = tmp_path / "echo_ok.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    class _Deps:
        cookies = {}
        extra = {
            "provider_instances": {
                "provider": {
                    "default": {
                        "provider_type": "provider",
                        "instance_name": "default",
                        "base_url": "https://provider.example.com/platform-api",
                        "auth_type": "user_token",
                        "cookie": "provider-session-cookie",
                        "password": "super-secret-password",
                        "user_token": "fake-provider-user-token",
                    }
                }
            }
        }

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        provider_type="provider",
        tool_name="provider_list_flavors",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))
    captured = capsys.readouterr()

    assert result["success"] is True
    assert "tool_name=provider_list_flavors, provider_type=provider" in captured.out
    assert "[DEBUG] Set env var: PASSWORD=***..." in captured.out
    assert "[DEBUG] Set env var: COOKIE=***..." in captured.out
    assert "[DEBUG] Set env var: USER_TOKEN=***..." in captured.out
    assert "super-secret-password" not in captured.out
    assert "provider-session-cookie" not in captured.out
    assert "fake-provider-user-token" not in captured.out


def test_script_wrapper_exposes_provider_sso_runtime_context_to_script_environment(
    tmp_path: Path,
) -> None:
    script = tmp_path / "echo_sso.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({",
                "  'available': os.environ.get('ATLASCLAW_PROVIDER_SSO_AVAILABLE', ''),",
                "  'token': os.environ.get('ATLASCLAW_PROVIDER_SSO_TOKEN', ''),",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        cookies = {}
        extra = {
            "provider_sso_available": True,
            "provider_sso_token": "oidc-access-token",
            "provider_instance": {
                "provider_type": "generic",
                "instance_name": "default",
                "base_url": "https://provider.example.com/platform-api",
                "auth_type": "sso",
            },
        }

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        provider_type="generic",
        tool_name="generic_list_catalogs",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload == {
        "available": "1",
        "token": "oidc-access-token",
    }


def test_script_wrapper_blocks_submit_request_without_explicit_confirmation(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "submit-ran.txt"
    script = tmp_path / "submit.py"
    script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
                "print('submitted')",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        user_message = "This is a UI automation check for provider user_token access."
        cookies = {}
        extra = {}

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        tool_name="provider_submit_request",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is False
    assert "explicit user confirmation is required" in result["error"].lower()
    assert not marker.exists()


def test_script_wrapper_allows_submit_request_with_explicit_confirmation(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "submit-ran.txt"
    script = tmp_path / "submit.py"
    script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
                "print('submitted')",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        user_message = "The parameters are correct. Please submit the request now."
        cookies = {}
        extra = {}

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        tool_name="provider_submit_request",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    assert result["output"] == "submitted\n"
    assert marker.read_text(encoding="utf-8") == "ran"


def test_script_wrapper_blocks_submit_intent_without_explicit_confirmation(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "submit-ran.txt"
    script = tmp_path / "submit.py"
    script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
                "print('submitted')",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        user_message = "Submit now."
        cookies = {}
        extra = {}

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        tool_name="provider_submit_request",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is False
    assert "explicit user confirmation is required" in result["error"].lower()
    assert not marker.exists()
