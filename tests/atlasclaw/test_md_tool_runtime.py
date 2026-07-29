# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from app.atlasclaw.skills.md_tool_runtime import (
    ScriptInvocationConfig,
    _extract_parameters_schema,
    create_script_wrapper,
    load_handler_from_file,
    register_executable_tools_from_md,
)
from app.atlasclaw.skills.registry import MdSkillEntry, SkillMetadata, SkillRegistry
from app.atlasclaw.tools.providers.instance_tools import (
    clear_recorded_provider_instance_selections,
    get_recorded_provider_instance_selection,
    record_provider_instance_selection,
)


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


def test_script_wrapper_exposes_request_timezone_to_script_environment(tmp_path: Path) -> None:
    script = tmp_path / "echo_timezone.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({'timezone': os.environ.get('ATLASCLAW_TIMEZONE', '')}))",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        extra = {"context": {"timezone": "America/New_York"}}

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(script)
    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload["timezone"] == "America/New_York"


def test_script_wrapper_omits_unsafe_and_inherited_timezones(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "echo_timezone.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({'timezone': os.environ.get('ATLASCLAW_TIMEZONE', '')}))",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLASCLAW_TIMEZONE", "Asia/Shanghai")

    class _Deps:
        extra = {"context": {"timezone": "America/New_York\x00UTC"}}

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(script)
    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    assert json.loads(result["output"].strip())["timezone"] == ""

    _Deps.extra = {"context": {"timezone": "Invalid\nTimezone"}}
    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    assert json.loads(result["output"].strip())["timezone"] == ""

    _Deps.extra = {"context": {}}
    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    assert json.loads(result["output"].strip())["timezone"] == ""


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


def test_script_wrapper_timeout_does_not_block_the_event_loop(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(1)\nprint('late')\n", encoding="utf-8")
    wrapper = create_script_wrapper(
        script,
        invocation_config=ScriptInvocationConfig(timeout_seconds=0.05),
    )

    async def run_scenario() -> tuple[dict, bool]:
        ticked = False

        async def ticker() -> None:
            nonlocal ticked
            await asyncio.sleep(0.01)
            ticked = True

        result, _ = await asyncio.gather(wrapper(), ticker())
        return result, ticked

    result, ticked = asyncio.run(run_scenario())

    assert ticked is True
    assert result == {"success": False, "error": "Script execution timed out"}


def test_script_wrapper_revalidates_embed_scope_before_provider_io(tmp_path: Path) -> None:
    marker = tmp_path / "provider-ran.txt"
    script = tmp_path / "provider.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    guard_calls: list[str] = []

    async def deny_stale(tool_name: str) -> None:
        guard_calls.append(tool_name)
        raise PermissionError("stale page")

    class _Deps:
        cookies = {}
        extra = {"_provider_io_guard": deny_stale}

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(script, tool_name="provider_mutate")
    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is False
    assert "stale page" in result["error"]
    assert guard_calls == ["provider_mutate"]
    assert marker.exists() is False


def test_function_handler_revalidates_embed_scope_before_provider_io(tmp_path: Path) -> None:
    marker = tmp_path / "function-ran.txt"
    module = tmp_path / "provider.py"
    module.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "async def execute(ctx=None):",
                f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
                "    return {'success': True}",
            ]
        ),
        encoding="utf-8",
    )

    async def deny_stale(_tool_name: str) -> None:
        raise PermissionError("stale page")

    class _Deps:
        extra = {"_provider_io_guard": deny_stale}

    class _Ctx:
        deps = _Deps()

    handler = load_handler_from_file(
        module,
        "execute",
        provider_type="example",
        tool_name="example_execute",
    )

    with pytest.raises(PermissionError, match="stale page"):
        asyncio.run(handler(ctx=_Ctx()))
    assert marker.exists() is False


def test_cancelled_script_wrapper_kills_child_process(tmp_path: Path) -> None:
    started = tmp_path / "started.txt"
    finished = tmp_path / "finished.txt"
    script = tmp_path / "slow.py"
    script.write_text(
        "\n".join(
            [
                "import time",
                "from pathlib import Path",
                f"Path({str(started)!r}).write_text('started', encoding='utf-8')",
                "time.sleep(1)",
                f"Path({str(finished)!r}).write_text('finished', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    wrapper = create_script_wrapper(script)

    async def run_scenario() -> None:
        task = asyncio.create_task(wrapper())
        for _ in range(100):
            if started.exists():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)

    asyncio.run(run_scenario())

    assert started.exists() is True
    assert finished.exists() is False


def test_script_wrapper_stops_stream_at_output_limit(tmp_path: Path) -> None:
    finished = tmp_path / "finished.txt"
    script = tmp_path / "noisy.py"
    script.write_text(
        "\n".join(
            [
                "import sys, time",
                "from pathlib import Path",
                "sys.stdout.write('x' * 65536)",
                "sys.stdout.flush()",
                "time.sleep(1)",
                f"Path({str(finished)!r}).write_text('finished', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    wrapper = create_script_wrapper(
        script,
        invocation_config=ScriptInvocationConfig(max_output_bytes=1024),
    )

    result = asyncio.run(wrapper())

    assert result == {
        "success": False,
        "error": "Script output exceeded the configured limit",
    }
    assert finished.exists() is False


def test_script_wrapper_sanitizes_provider_http_auth_errors(tmp_path: Path) -> None:
    script = tmp_path / "auth_fail.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "print('[ERROR] Request failed: 401 Client Error: for url: https://provider.example/api')",
                "sys.exit(1)",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = create_script_wrapper(
        script,
        provider_type="provider",
        tool_name="provider_list_pending",
    )
    result = asyncio.run(wrapper())

    assert result["success"] is False
    assert "401 Client Error" not in result["output"]
    assert "Provider authentication failed" in result["output"]
    assert "user_token" not in result["output"]
    assert "personal provider access credential" not in result["output"]


def test_md_tool_preserves_explicit_zero_argument_object_schema() -> None:
    metadata = {
        "tool_read_current_parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    }

    schema = _extract_parameters_schema(metadata, tool_id="read_current")

    assert schema == {
        "type": "object",
        "properties": {},
    }
    assert _extract_parameters_schema(
        {"tool_read_current_parameters": {"properties": {}}},
        tool_id="read_current",
    ) == {}


def test_artifact_md_tool_defaults_to_tool_only_result_mode(tmp_path: Path) -> None:
    skill_dir = tmp_path / "artifact"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "handler.py").write_text(
        "\n".join(
            [
                "def create_handler(ctx=None, **kwargs):",
                "    return {'success': True, 'artifact_path': '/tmp/example.txt'}",
            ]
        ),
        encoding="utf-8",
    )
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: artifact\n---\n", encoding="utf-8")
    entry = MdSkillEntry(
        name="artifact",
        description="Create an artifact",
        file_path=str(skill_file),
        qualified_name="artifact",
        metadata={
            "tool_create_name": "artifact_create",
            "tool_create_entrypoint": "scripts/handler.py:create_handler",
            "tool_create_capability_class": "artifact:custom",
        },
    )
    registry = SkillRegistry()

    register_executable_tools_from_md(
        registry=registry,
        skill_metadata_cls=SkillMetadata,
        entry=entry,
        logger=logging.getLogger(__name__),
    )

    metadata, _handler = registry.get("artifact_create")
    assert metadata.result_mode == "tool_only_ok"


def test_artifact_md_tool_rejects_home_relative_user_requested_output(tmp_path: Path) -> None:
    skill_dir = tmp_path / "artifact"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    marker = tmp_path / "handler-ran.txt"
    (scripts_dir / "handler.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "def create_handler(ctx=None, **kwargs):",
                f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
                "    return {'success': True, 'artifact_path': 'safe.pptx'}",
            ]
        ),
        encoding="utf-8",
    )
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: artifact\n---\n", encoding="utf-8")
    entry = MdSkillEntry(
        name="artifact",
        description="Create an artifact",
        file_path=str(skill_file),
        qualified_name="artifact",
        metadata={
            "tool_create_name": "artifact_create",
            "tool_create_entrypoint": "scripts/handler.py:create_handler",
            "tool_create_capability_class": "artifact:pptx",
        },
    )
    registry = SkillRegistry()

    register_executable_tools_from_md(
        registry=registry,
        skill_metadata_cls=SkillMetadata,
        entry=entry,
        logger=logging.getLogger(__name__),
    )
    _metadata, handler = registry.get("artifact_create")

    class _Deps:
        user_message = "Generate a PPTX at ~/bad.pptx"

    class _Ctx:
        deps = _Deps()

    result = asyncio.run(handler(ctx=_Ctx(), output_filename="safe.pptx", items=[]))

    assert result["success"] is False
    assert "home-relative output paths are not allowed" in result["error"]
    assert not marker.exists()


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
            },
            "provider_type": "provider",
            "provider_instance_name": "default",
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


def test_script_wrapper_blocks_provider_script_without_selected_instance(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "script-ran.txt"
    script = tmp_path / "echo_default_instance.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
                "print(json.dumps({",
                "  'provider_type': os.environ.get('ATLASCLAW_PROVIDER_TYPE', ''),",
                "  'provider_instance': os.environ.get('ATLASCLAW_PROVIDER_INSTANCE', ''),",
                "  'base_url': os.environ.get('BASE_URL', ''),",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        cookies = {}
        extra = {
            "provider_instances": {
                "provider": {
                    "prod": {
                        "base_url": "https://provider.example.com/prod",
                        "usage_hint": "Use for production provider requests.",
                    },
                    "dev": {
                        "base_url": "https://provider.example.com/dev",
                        "usage_hint": "Use for development provider requests.",
                    },
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

    assert result["success"] is False
    assert result["error"] == "Provider instance selection required"
    assert "Provider 'provider' has 2 instances:" in result["output"]
    assert "prod" in result["output"]
    assert "Use for production provider requests." in result["output"]
    assert "dev" in result["output"]
    assert "Use for development provider requests." in result["output"]
    assert not marker.exists()


def test_script_wrapper_blocks_provider_script_without_visible_instance(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "script-ran.txt"
    script = tmp_path / "requires_instance.py"
    script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
                "print('ran')",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        cookies = {}
        extra = {"provider_instances": {}}

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        provider_type="provider",
        tool_name="provider_list_flavors",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is False
    assert result["error"] == "Provider instance selection required"
    assert "Provider instance selection required for provider 'provider'." in result["output"]
    assert not marker.exists()


def test_script_wrapper_uses_session_sticky_provider_instance(
    tmp_path: Path,
) -> None:
    script = tmp_path / "echo_instance.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({",
                "  'provider_type': os.environ.get('ATLASCLAW_PROVIDER_TYPE', ''),",
                "  'provider_instance': os.environ.get('ATLASCLAW_PROVIDER_INSTANCE', ''),",
                "  'base_url': os.environ.get('BASE_URL', ''),",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        cookies = {}
        extra = {
            "provider_instance_selections": {"provider": "dev"},
            "provider_instances": {
                "provider": {
                    "prod": {"base_url": "https://provider.example.com/prod"},
                    "dev": {"base_url": "https://provider.example.com/dev"},
                }
            },
        }

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        provider_type="provider",
        tool_name="provider_list_flavors",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload == {
        "provider_type": "provider",
        "provider_instance": "dev",
        "base_url": "https://provider.example.com/dev",
    }


def test_script_wrapper_uses_recorded_same_run_provider_selection(
    tmp_path: Path,
) -> None:
    script = tmp_path / "echo_recorded_instance.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({",
                "  'provider_type': os.environ.get('ATLASCLAW_PROVIDER_TYPE', ''),",
                "  'provider_instance': os.environ.get('ATLASCLAW_PROVIDER_INSTANCE', ''),",
                "  'base_url': os.environ.get('BASE_URL', ''),",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        session_key = "agent:main:user:test:web:dm:test:topic:same-run-selection"
        cookies = {}
        extra = {
            "run_id": "run-same-run-selection",
            "provider_instances": {
                "provider": {
                    "prod": {"base_url": "https://provider.example.com/prod"},
                    "dev": {"base_url": "https://provider.example.com/dev"},
                }
            },
        }

    class _Ctx:
        deps = _Deps()

    record_provider_instance_selection(_Ctx.deps, "provider", "dev")
    wrapper = create_script_wrapper(
        script,
        provider_type="provider",
        tool_name="provider_list_flavors",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload == {
        "provider_type": "provider",
        "provider_instance": "dev",
        "base_url": "https://provider.example.com/dev",
    }
    clear_recorded_provider_instance_selections(_Ctx.deps)


def test_recorded_provider_selection_is_scoped_to_run_id() -> None:
    class _RunOneDeps:
        session_key = "agent:main:user:test:web:dm:test:topic:selection-cache"
        extra = {"run_id": "run-one"}

    class _RunTwoDeps:
        session_key = "agent:main:user:test:web:dm:test:topic:selection-cache"
        extra = {"run_id": "run-two"}

    record_provider_instance_selection(_RunOneDeps(), "provider", "dev")

    assert get_recorded_provider_instance_selection(_RunOneDeps(), "provider") == "dev"
    assert get_recorded_provider_instance_selection(_RunTwoDeps(), "provider") == ""

    clear_recorded_provider_instance_selections(_RunOneDeps())
    assert get_recorded_provider_instance_selection(_RunOneDeps(), "provider") == ""


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


def test_script_wrapper_exposes_provider_cookie_runtime_context_to_script_environment(
    tmp_path: Path,
) -> None:
    script = tmp_path / "echo_cookie.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({",
                "  'available': os.environ.get('ATLASCLAW_PROVIDER_COOKIE_AVAILABLE', ''),",
                "  'token': os.environ.get('ATLASCLAW_PROVIDER_COOKIE_TOKEN', ''),",
                "  'cookie': os.environ.get('COOKIE', ''),",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    class _Deps:
        cookies = {}
        extra = {
            "provider_cookie_available": True,
            "provider_cookie_token": "request-cookie-token",
            "provider_instance": {
                "provider_type": "generic",
                "instance_name": "default",
                "base_url": "https://provider.example.com/platform-api",
                "auth_type": "cookie",
                "cookie": "request-cookie-token",
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
        "token": "request-cookie-token",
        "cookie": "request-cookie-token",
    }


def test_script_wrapper_exposes_robot_provider_metadata_to_script_environment(
    tmp_path: Path,
) -> None:
    script = tmp_path / "echo_robot.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({",
                "  'provider_type': os.environ.get('ATLASCLAW_PROVIDER_TYPE', ''),",
                "  'provider_instance': os.environ.get('ATLASCLAW_PROVIDER_INSTANCE', ''),",
                "  'robot_profile': os.environ.get('ATLASCLAW_ROBOT_PROFILE', ''),",
                "  'provider_config': json.loads(os.environ.get('ATLASCLAW_PROVIDER_CONFIG', '{}')),",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    runtime_provider_config = {
        "smartcmp": {
            "cmp": {
                "provider_type": "smartcmp",
                "instance_name": "cmp",
                "base_url": "https://cmp.example.com/platform-api",
                "auth_type": "provider_token",
                "provider_token": "cmp_tk_robot_secret",
            }
        }
    }

    class _Deps:
        cookies = {}
        extra = {
            "provider_config": runtime_provider_config,
            "provider_type": "smartcmp",
            "provider_instance_name": "cmp",
            "robot_profile": "preapproval_bot",
            "provider_instance": runtime_provider_config["smartcmp"]["cmp"],
        }

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        provider_type="smartcmp",
        tool_name="smartcmp_list_pending",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    payload = json.loads(result["output"].strip())
    assert payload["provider_type"] == "smartcmp"
    assert payload["provider_instance"] == "cmp"
    assert payload["robot_profile"] == "preapproval_bot"
    assert payload["provider_config"] == runtime_provider_config


def test_script_wrapper_exports_only_selected_provider_instance_config(
    tmp_path: Path,
) -> None:
    script = tmp_path / "echo_scoped_provider_config.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps(json.loads(os.environ.get('ATLASCLAW_PROVIDER_CONFIG', '{}'))))",
            ]
        ),
        encoding="utf-8",
    )
    provider_config = {
        "smartcmp": {
            "cmp": {
                "provider_type": "smartcmp",
                "instance_name": "cmp",
                "base_url": "https://cmp.example.com/platform-api",
            },
            "other": {
                "provider_type": "smartcmp",
                "instance_name": "other",
                "base_url": "https://other.example.com/platform-api",
            },
        }
    }

    class _Deps:
        cookies = {}
        extra = {
            "provider_config": provider_config,
            "provider_instances": provider_config,
            "provider_type": "smartcmp",
            "provider_instance_name": "cmp",
            "provider_instance": provider_config["smartcmp"]["cmp"],
        }

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        provider_type="smartcmp",
        tool_name="smartcmp_list_services",
    )

    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    assert json.loads(result["output"].strip()) == {
        "smartcmp": {
            "cmp": provider_config["smartcmp"]["cmp"],
        }
    }


def test_request_cookie_only_wrapper_excludes_shared_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    script = tmp_path / "echo_context_env.py"
    script.write_text(
        "\n".join(
            [
                "import json, os",
                "print(json.dumps({",
                "  'config': json.loads(os.environ.get('ATLASCLAW_PROVIDER_CONFIG', '{}')),",
                "  'username': os.environ.get('USERNAME', ''),",
                "  'password': os.environ.get('PASSWORD', ''),",
                "  'cookie': json.loads(os.environ.get('ATLASCLAW_COOKIES', '{}')),",
                "  'unknown_secret': os.environ.get('EXAMPLE_ACCESS_TOKEN', ''),",
                "}))",
            ]
        ),
        encoding="utf-8",
    )
    provider_config = {
        "smartcmp": {
            "cmp": {
                "base_url": "https://cmp.example.com/platform-api",
                "timeout": 30,
                "auth_type": "credential",
                "username": "shared-user",
                "password": "shared-password",
            }
        }
    }
    monkeypatch.setenv("EXAMPLE_ACCESS_TOKEN", "must-not-cross-resolver-boundary")

    class _Deps:
        cookies = {"CloudChef-Authenticate": "request-cookie"}
        extra = {
            "provider_instances": provider_config,
            "provider_type": "smartcmp",
            "provider_instance_name": "cmp",
            "provider_instance": provider_config["smartcmp"]["cmp"],
        }

    class _Ctx:
        deps = _Deps()

    wrapper = create_script_wrapper(
        script,
        provider_type="smartcmp",
        invocation_config=ScriptInvocationConfig(request_cookie_only=True),
    )
    result = asyncio.run(wrapper(ctx=_Ctx()))

    assert result["success"] is True
    payload = json.loads(result["output"])
    assert payload["config"] == {
        "smartcmp": {
            "cmp": {
                "base_url": "https://cmp.example.com/platform-api",
                "timeout": 30,
            }
        }
    }
    assert payload["username"] == ""
    assert payload["password"] == ""
    assert payload["cookie"] == {"CloudChef-Authenticate": "request-cookie"}
    assert payload["unknown_secret"] == ""


def test_script_wrapper_leaves_submit_confirmation_to_model_routing(
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
        user_message = "请确认并提交申请"
        cookies = {}
        extra = {
            "tool_intent_plan": {
                "target_provider_skill_names": ["cmp.request"],
                "target_provider_instances": ["smartcmp.cmp"],
            }
        }

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
