# -*- coding: utf-8 -*-
"""Runtime helpers for executable tools declared in markdown skills."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional


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
            return create_script_wrapper(py_file, provider_type)

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
        return create_script_wrapper(py_file, provider_type)
    finally:
        if inserted:
            try:
                sys.path.remove(scripts_dir)
            except ValueError:
                pass


def create_script_wrapper(py_file: Path, provider_type: Optional[str] = None) -> Callable:
    """Create a wrapper function that executes a script file."""

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

    provider_type = str(entry.metadata.get("provider_type", "")).strip() or entry.provider or None
    if not allow_script_execution and attr_name == "handler":
        logger.info(
            "Skipping md tool %s from %s: script-backed markdown tools are disabled by default",
            tool_name,
            entry.name,
        )
        return
    try:
        handler = load_handler_from_file(
            py_file,
            attr_name,
            provider_type,
            allow_script_execution=allow_script_execution,
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
    meta = skill_metadata_cls(
        name=tool_name,
        description=description,
        category=str(entry.metadata.get("category", "skill")),
        location=entry.location,
        provider_type=provider_type,
        instance_required=str(entry.metadata.get("instance_required", "")).lower() in ("1", "true", "yes"),
    )
    registry.register(meta, handler)
    registered.add(tool_name)
