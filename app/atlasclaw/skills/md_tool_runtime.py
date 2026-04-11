# -*- coding: utf-8 -*-
"""Runtime helpers for executable tools declared in markdown skills."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class ScriptInvocationConfig:
    """Metadata-driven script invocation hints for markdown executable tools."""

    positional_args: tuple[str, ...] = ()
    split_args: tuple[str, ...] = ()
    flag_name_overrides: dict[str, str] = field(default_factory=dict)


def parse_entrypoint(entrypoint: str) -> tuple[str, str]:
    """Parse module entrypoint into (module_path, attr_name)."""
    if ":" in entrypoint:
        module_path, attr_name = entrypoint.rsplit(":", 1)
        return module_path.strip(), attr_name.strip() or "handler"
    return entrypoint.strip(), "handler"


def should_override_location(existing_location: str, new_location: str) -> bool:
    """Return whether new location should override existing by priority."""
    priority = {"built-in": 0, "external": 1, "user": 2, "workspace": 3}
    return priority.get(new_location, 0) >= priority.get(existing_location, 0)


def load_handler_from_file(
    py_file: Path,
    attr_name: str,
    provider_type: Optional[str] = None,
    *,
    allow_script_execution: bool = False,
    invocation_config: Optional[ScriptInvocationConfig] = None,
) -> Callable:
    """Load callable handler from file or fallback to script wrapper."""
    scripts_dir = str(py_file.parent)
    inserted = False
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
        inserted = True

    try:
        if attr_name == "handler":
            if not allow_script_execution:
                raise RuntimeError("script-backed markdown tools are disabled")
            return create_script_wrapper(
                py_file,
                provider_type,
                invocation_config=invocation_config,
            )

        module_hash = hashlib.sha1(str(py_file).encode("utf-8")).hexdigest()[:12]
        module_name = f"atlasclaw_md_skill_{module_hash}_{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {py_file}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handler = getattr(module, attr_name, None)
        if handler is not None and callable(handler):
            return handler
        if not allow_script_execution:
            raise RuntimeError("script-backed markdown tools are disabled")
        return create_script_wrapper(
            py_file,
            provider_type,
            invocation_config=invocation_config,
        )
    finally:
        if inserted:
            try:
                sys.path.remove(scripts_dir)
            except ValueError:
                pass


def create_script_wrapper(
    py_file: Path,
    provider_type: Optional[str] = None,
    *,
    invocation_config: Optional[ScriptInvocationConfig] = None,
) -> Callable:
    """Create a wrapper function that executes a script file."""
    config = invocation_config or ScriptInvocationConfig()

    async def script_handler(ctx=None, **kwargs) -> dict:
        import os

        env = os.environ.copy()

        if ctx is not None and hasattr(ctx, "deps") and hasattr(ctx.deps, "cookies"):
            cookies = ctx.deps.cookies
            if cookies:
                try:
                    env["ATLASCLAW_COOKIES"] = json.dumps(cookies)
                    if hasattr(ctx.deps, "user_info") and ctx.deps.user_info:
                        print(
                            f"[DEBUG] Set ATLASCLAW_COOKIES for user={ctx.deps.user_info.user_id}, "
                            f"cookies={list(cookies.keys())}"
                        )
                except (TypeError, ValueError) as exc:
                    print(f"[WARNING] Failed to serialize cookies: {exc}")

        if ctx is not None and hasattr(ctx, "deps") and hasattr(ctx.deps, "extra"):
            extra = ctx.deps.extra
            provider_config = extra.get("provider_config", {}) if extra else {}
            if provider_config:
                try:
                    env["ATLASCLAW_PROVIDER_CONFIG"] = json.dumps(provider_config)
                    print(
                        "[DEBUG] Set ATLASCLAW_PROVIDER_CONFIG with providers: "
                        f"{list(provider_config.keys())}"
                    )
                except (TypeError, ValueError) as exc:
                    print(f"[WARNING] Failed to serialize provider_config: {exc}")

        if ctx is not None and hasattr(ctx, "deps") and hasattr(ctx.deps, "extra"):
            extra = ctx.deps.extra
            print(f"[DEBUG] Tool execution: provider_type={provider_type}")
            print(f"[DEBUG] ctx.deps.extra keys: {list(extra.keys())}")

            provider_instance = extra.get("provider_instance")
            if provider_instance:
                print(f"[DEBUG] Using selected provider_instance: {provider_instance}")
                for key, value in provider_instance.items():
                    if value is not None and key not in ("token", "secret"):
                        env[key.upper()] = str(value)
            elif "provider_instances" in extra:
                provider_instances = extra["provider_instances"]
                print(f"[DEBUG] Available provider_types: {list(provider_instances.keys())}")

                target_provider = provider_type
                if target_provider and target_provider in provider_instances:
                    instances = provider_instances[target_provider]
                    print(f"[DEBUG] Found instances for {target_provider}: {list(instances.keys())}")
                    if instances:
                        default_instance = list(instances.values())[0]
                        print(f"[DEBUG] Using instance config: {list(default_instance.keys())}")
                        for key, value in default_instance.items():
                            if value is not None and key not in ("token", "secret"):
                                env[key.upper()] = str(value)
                                print(f"[DEBUG] Set env var: {key.upper()}={'***' if key in ('password',) else str(value)[:50]}...")
                else:
                    print("[DEBUG] No specific provider_type, using first available")
                    for _, instances in provider_instances.items():
                        if instances:
                            default_instance = list(instances.values())[0]
                            for key, value in default_instance.items():
                                if value is not None and key not in ("token", "secret"):
                                    env[key.upper()] = str(value)
                            break

        for key, value in kwargs.items():
            if value is not None:
                env[key.upper()] = str(value)

        if py_file.suffix == ".py":
            cmd = [sys.executable, str(py_file)]
        elif py_file.suffix in [".sh", ".bash"]:
            cmd = ["bash", str(py_file)]
        elif py_file.suffix == ".ps1":
            cmd = ["powershell", "-File", str(py_file)]
        else:
            cmd = [str(py_file)]

        cmd.extend(_build_script_command_arguments(kwargs=kwargs, config=config))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
                cwd=str(py_file.parent),
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[STDERR] {result.stderr}"
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "output": output,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Script execution timed out"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    return script_handler


def register_executable_tools_from_md(
    *,
    registry: Any,
    entry: Any,
    skill_metadata_cls: Any,
    logger: Any,
    allow_script_execution: bool = False,
) -> None:
    """Register executable tools declared in markdown skill metadata."""
    skill_dir = Path(entry.file_path).parent
    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}

    registered: set[str] = set()

    single_tool_name = str(metadata.get("tool_name", "")).strip()
    single_entrypoint = str(metadata.get("entrypoint", "")).strip()
    if single_tool_name and single_entrypoint:
        _register_md_tool_entry(
            registry=registry,
            skill_metadata_cls=skill_metadata_cls,
            logger=logger,
            tool_name=single_tool_name,
            entrypoint=single_entrypoint,
            tool_id="default",
            entry=entry,
            skill_dir=skill_dir,
            registered=registered,
            allow_script_execution=allow_script_execution,
        )

    ids: set[str] = set()
    for key in metadata.keys():
        if key.startswith("tool_") and key.endswith("_name"):
            ids.add(key[len("tool_") : -len("_name")])
        elif key.startswith("tool_") and key.endswith("_entrypoint"):
            ids.add(key[len("tool_") : -len("_entrypoint")])

    for tool_id in sorted(ids):
        tool_name = str(metadata.get(f"tool_{tool_id}_name", "")).strip()
        entrypoint = str(metadata.get(f"tool_{tool_id}_entrypoint", "")).strip()
        tool_description = str(metadata.get(f"tool_{tool_id}_description", "")).strip()
        if not tool_name or not entrypoint:
            logger.warning(
                "Skipping md tool declaration for skill %s: incomplete pair for id '%s'",
                entry.name,
                tool_id,
            )
            continue

        _register_md_tool_entry(
            registry=registry,
            skill_metadata_cls=skill_metadata_cls,
            logger=logger,
            tool_name=tool_name,
            entrypoint=entrypoint,
            tool_description=tool_description,
            tool_id=tool_id,
            entry=entry,
            skill_dir=skill_dir,
            registered=registered,
            allow_script_execution=allow_script_execution,
        )

    if registered:
        registry._md_skill_tools[entry.qualified_name] = registered


def _register_md_tool_entry(
    *,
    registry: Any,
    skill_metadata_cls: Any,
    logger: Any,
    tool_name: str,
    entrypoint: str,
    entry: Any,
    skill_dir: Path,
    registered: set[str],
    tool_description: str = "",
    tool_id: str = "",
    allow_script_execution: bool = False,
) -> None:
    module_path, attr_name = parse_entrypoint(entrypoint)
    py_file = (skill_dir / module_path).resolve()
    if not py_file.is_file():
        logger.warning(
            "Skipping md tool %s from %s: entrypoint file not found: %s",
            tool_name,
            entry.name,
            py_file,
        )
        return

    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
    provider_type = str(metadata.get("provider_type", "")).strip() or entry.provider or None
    if not allow_script_execution and attr_name == "handler":
        logger.info(
            "Skipping md tool %s from %s: script-backed markdown tools are disabled by default",
            tool_name,
            entry.name,
        )
        return
    try:
        invocation_config = _extract_script_invocation_config(metadata, tool_id=tool_id)
        handler = load_handler_from_file(
            py_file,
            attr_name,
            provider_type,
            allow_script_execution=allow_script_execution,
            invocation_config=invocation_config,
        )
    except Exception as exc:
        logger.warning(
            "Skipping md tool %s from %s: failed loading handler %s (%s)",
            tool_name,
            entry.name,
            entrypoint,
            exc,
        )
        return

    description = tool_description if tool_description else entry.description
    group_ids = _extract_group_ids(metadata, entry.provider, tool_id=tool_id)
    capability_class = _extract_capability_class(metadata, provider_type, tool_id=tool_id)
    priority = _extract_priority(metadata, tool_id=tool_id)
    parameters_schema = _extract_parameters_schema(metadata, tool_id=tool_id)
    source = "provider" if provider_type else "md_skill"
    meta = skill_metadata_cls(
        name=tool_name,
        description=description,
        category=str(metadata.get("category", "skill")),
        location=entry.location,
        provider_type=provider_type,
        instance_required=str(metadata.get("instance_required", "")).lower() in ("1", "true", "yes"),
        source=source,
        group_ids=group_ids,
        capability_class=capability_class,
        priority=priority,
        parameters_schema=parameters_schema,
        aliases=_extract_string_sequence(
            metadata.get(f"tool_{tool_id}_aliases") if tool_id else metadata.get("aliases")
        ),
        keywords=_extract_string_sequence(
            metadata.get(f"tool_{tool_id}_keywords") if tool_id else metadata.get("triggers")
        ),
        use_when=_extract_string_sequence(
            metadata.get(f"tool_{tool_id}_use_when") if tool_id else metadata.get("use_when")
        ),
        avoid_when=_extract_string_sequence(
            metadata.get(f"tool_{tool_id}_avoid_when") if tool_id else metadata.get("avoid_when")
        ),
        result_mode=_extract_result_mode(metadata, tool_id=tool_id),
    )
    registry.register(meta, handler)
    registered.add(tool_name)


def _extract_group_ids(
    metadata: dict[str, Any],
    provider_type: Optional[str],
    *,
    tool_id: str = "",
) -> list[str]:
    values: list[Any] = []
    for key in ("group", "groups", "tool_group", "tool_groups"):
        if key in metadata:
            values.append(metadata.get(key))

    if tool_id:
        for key in (f"tool_{tool_id}_group", f"tool_{tool_id}_groups"):
            if key in metadata:
                values.append(metadata.get(key))

    normalized: list[str] = []
    seen: set[str] = set()

    def _append(group: str) -> None:
        name = str(group or "").strip()
        if not name:
            return
        if not name.startswith("group:"):
            name = f"group:{name}"
        if name in seen:
            return
        seen.add(name)
        normalized.append(name)

    for value in values:
        if isinstance(value, str):
            _append(value)
            continue
        if isinstance(value, list):
            for item in value:
                _append(str(item))
            continue
        if isinstance(value, dict):
            for group_name, members in value.items():
                if not tool_id:
                    continue
                member_names: list[str] = []
                if isinstance(members, str):
                    member_names = [members]
                elif isinstance(members, list):
                    member_names = [str(item) for item in members]
                if tool_id in member_names:
                    _append(group_name)

    if provider_type:
        _append(provider_type)
    return normalized


def _extract_capability_class(
    metadata: dict[str, Any],
    provider_type: Optional[str],
    *,
    tool_id: str = "",
) -> str:
    if tool_id:
        per_tool = str(metadata.get(f"tool_{tool_id}_capability_class", "") or "").strip()
        if per_tool:
            return per_tool
    explicit = str(metadata.get("capability_class", "") or "").strip()
    if explicit:
        return explicit
    normalized_provider = str(provider_type or "").strip()
    if normalized_provider:
        return f"provider:{normalized_provider}"
    return "skill"


def _extract_priority(metadata: dict[str, Any], *, tool_id: str = "") -> int:
    candidate: Any = metadata.get("priority", 100)
    if tool_id and f"tool_{tool_id}_priority" in metadata:
        candidate = metadata.get(f"tool_{tool_id}_priority", 100)
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return 100


def _extract_parameters_schema(metadata: dict[str, Any], *, tool_id: str = "") -> dict[str, Any]:
    """Return the per-tool JSON schema declared in markdown metadata, if any."""
    candidates: list[Any] = []
    if tool_id:
        candidates.append(metadata.get(f"tool_{tool_id}_parameters"))
    else:
        candidates.append(metadata.get("parameters"))
        candidates.append(metadata.get("tool_parameters"))

    for candidate in candidates:
        schema = _coerce_parameters_schema(candidate)
        if schema:
            return schema
    return {}


def _extract_result_mode(metadata: dict[str, Any], *, tool_id: str = "") -> str:
    candidate: Any = metadata.get("result_mode", "llm")
    if tool_id and f"tool_{tool_id}_result_mode" in metadata:
        candidate = metadata.get(f"tool_{tool_id}_result_mode", "llm")
    normalized = str(candidate or "").strip().lower()
    return normalized or "llm"


def _extract_script_invocation_config(
    metadata: dict[str, Any],
    *,
    tool_id: str = "",
) -> ScriptInvocationConfig:
    """Extract CLI invocation hints for script-backed markdown tools."""
    positional_args = _extract_string_sequence(
        metadata.get(f"tool_{tool_id}_cli_positional") if tool_id else metadata.get("cli_positional")
    )
    split_args = _extract_string_sequence(
        metadata.get(f"tool_{tool_id}_cli_split") if tool_id else metadata.get("cli_split")
    )
    raw_flag_overrides = (
        metadata.get(f"tool_{tool_id}_cli_flag_overrides")
        if tool_id
        else metadata.get("cli_flag_overrides")
    )
    flag_name_overrides: dict[str, str] = {}
    if isinstance(raw_flag_overrides, dict):
        for key, value in raw_flag_overrides.items():
            normalized_key = str(key or "").strip()
            normalized_value = str(value or "").strip()
            if normalized_key and normalized_value:
                flag_name_overrides[normalized_key] = normalized_value
    return ScriptInvocationConfig(
        positional_args=tuple(positional_args),
        split_args=tuple(split_args),
        flag_name_overrides=flag_name_overrides,
    )


def _extract_string_sequence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        payload = value.strip()
        if not payload:
            return []
        if payload.startswith("["):
            try:
                value = json.loads(payload)
            except json.JSONDecodeError:
                return [payload]
        else:
            return [payload]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _build_script_command_arguments(
    *,
    kwargs: dict[str, Any],
    config: ScriptInvocationConfig,
) -> list[str]:
    """Serialize tool kwargs into CLI argv using metadata-driven hints."""
    if not kwargs:
        return []

    args: list[str] = []
    consumed: set[str] = set()
    split_names = {name for name in config.split_args if name}

    for name in config.positional_args:
        normalized_name = str(name or "").strip()
        if not normalized_name or normalized_name not in kwargs:
            continue
        consumed.add(normalized_name)
        args.extend(
            _serialize_cli_value(
                value=kwargs.get(normalized_name),
                split=normalized_name in split_names,
            )
        )

    for name, value in kwargs.items():
        normalized_name = str(name or "").strip()
        if not normalized_name or normalized_name in consumed or value is None:
            continue
        if isinstance(value, bool):
            if value:
                args.append(_resolve_cli_flag_name(normalized_name, config))
            continue
        serialized = _serialize_cli_value(
            value=value,
            split=normalized_name in split_names,
        )
        if not serialized:
            continue
        args.append(_resolve_cli_flag_name(normalized_name, config))
        args.extend(serialized)

    return args


def _resolve_cli_flag_name(name: str, config: ScriptInvocationConfig) -> str:
    override = str(config.flag_name_overrides.get(name, "") or "").strip()
    if override:
        return override
    return f"--{name.replace('_', '-')}"


def _serialize_cli_value(*, value: Any, split: bool) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        serialized: list[str] = []
        for item in value:
            serialized.extend(_serialize_cli_value(value=item, split=split))
        return serialized
    normalized = str(value).strip()
    if not normalized:
        return []
    if split:
        tokens = [token for token in normalized.replace(",", " ").split() if token]
        return tokens or [normalized]
    return [normalized]


def _coerce_parameters_schema(candidate: Any) -> dict[str, Any]:
    """Normalize markdown-frontmatter parameter declarations into an object JSON schema."""
    if isinstance(candidate, str):
        payload = candidate.strip()
        if not payload:
            return {}
        try:
            candidate = json.loads(payload)
        except json.JSONDecodeError:
            return {}

    if not isinstance(candidate, dict):
        return {}

    schema_type = str(candidate.get("type", "") or "").strip().lower()
    if schema_type and schema_type != "object":
        return {}
    properties = candidate.get("properties")
    if not isinstance(properties, dict) or not properties:
        return {}

    normalized: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    required = candidate.get("required")
    if isinstance(required, list):
        normalized["required"] = [str(item) for item in required if str(item).strip()]
    return normalized
