"""Skill registry and Markdown skill loading.

This module manages executable Python skills and Markdown-based skill metadata.
Markdown skills are discovered from multiple search roots with the following
precedence:

1. Workspace skills: `<workspace>/skills/`
2. User skills: `~/.atlasclaw/skills/`
3. Built-in skills bundled with the application
"""

from __future__ import annotations

import json
import inspect
import logging
import re
import hashlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any, Optional, TYPE_CHECKING

from pydantic import BaseModel
from pydantic_ai import RunContext

from app.atlasclaw.skills.frontmatter import parse_frontmatter
from app.atlasclaw.core.deps import SkillDeps

if TYPE_CHECKING:
    from pydantic_ai import Agent

logger = logging.getLogger(__name__)

# ---------- Skill name validation ----------

_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_DEFAULT_MAX_FILE_BYTES = 262144  # 256 KB


def validate_skill_name(
    name: str,
    *,
    parent_dir_name: Optional[str] = None,
) -> Optional[str]:
    """
    Validate a Markdown skill name.

    The validation rules match the OpenClaw naming constraints:

    - lowercase letters, digits, and single hyphens only
    - maximum length of 64 characters
    - no consecutive hyphens
    - parent directory match is optional (warning only, not enforced)

    Args:
        name: Candidate skill name.
        parent_dir_name: Optional parent directory name for structural checks.

    Returns:
        An error string when validation fails, otherwise `None`.
    """
    if not name:
        return "name is empty"
    if len(name) > _MAX_NAME_LENGTH:
        return f"name exceeds {_MAX_NAME_LENGTH} chars"
    if "--" in name:
        return "name contains consecutive hyphens '--'"
    if not _NAME_PATTERN.match(name):
        return "name must match [a-z0-9] with single hyphens only"
    # Note: parent directory name check is relaxed to allow flexible naming
    # e.g., directory "jira-bulk" can contain skill named "jira-bulk-operations"
    return None


class SkillMetadata(BaseModel):
    """
    Metadata for an executable Python skill.

    Attributes:
        name: Stable skill name.
        description: Human-readable skill description.
        category: Skill category used for grouping.
        requires_auth: Whether the skill requires authenticated access.
        timeout_seconds: Default execution timeout.
        location: Skill source, such as `built-in`, `user`, or `workspace`.
        provider_type: Optional provider type, for example `jira`.
        instance_required: Whether the provider instance must be selected first.
    """
    name: str
    description: str = ""
    category: str = "utility"
    requires_auth: bool = False
    timeout_seconds: int = 30
    location: str = "built-in"
    provider_type: Optional[str] = None
    instance_required: bool = False


@dataclass
class MdSkillEntry:
    """
    Metadata entry for a Markdown skill.

    Markdown skills are loaded from `SKILL.md` files and exposed through prompt
    context rather than direct tool registration.

    Attributes:
        name: Skill name.
        description: Skill description.
        file_path: Absolute path to the `SKILL.md` file.
        location: Skill source, such as `built-in`, `user`, or `workspace`.
        metadata: Additional frontmatter keys beyond `name` and `description`.
                  Supports both string values and lists (e.g., triggers, use_when).
    """

    name: str
    description: str
    file_path: str
    provider: str = ""
    qualified_name: str = ""
    location: str = "built-in"
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillRegistry:
    """
    Registry for executable skills and Markdown skill metadata.

    Example usage:
        ```python
        registry = SkillRegistry()
        
        # Register an executable skill.
        registry.register(
            SkillMetadata(name="query_vm", description="query virtual machines"),
            query_vm_handler,
        )
        
        # Build a metadata snapshot for PromptBuilder.
        skills = registry.snapshot()
        
        # Register skills on a PydanticAI agent.
        registry.register_to_agent(agent)
        
        # Execute a skill directly.
        result = await registry.execute("query_vm", '{"vm_id":"123"}', deps)
        ```
    """
    
    SEARCH_PATHS = [
        "{workspace}/skills/",   # Workspace skills take highest priority
        "~/.atlasclaw/skills/",    # User skills override built-in ones
        # Built-in skills are loaded separately
    ]
    
    def __init__(self, workspace: Optional[str] = None):
        """Initialize the registry."""
        self._skills: dict[str, tuple[SkillMetadata, Callable]] = {}
        self._md_skills: dict[str, MdSkillEntry] = {}
        self._md_skill_tools: dict[str, set[str]] = {}
        self._workspace = workspace
    
    def register(
        self,
        metadata: SkillMetadata,
        handler: Callable,
    ) -> None:
        """Register an executable skill handler."""
        self._skills[metadata.name] = (metadata, handler)
    
    def unregister(self, name: str) -> bool:
        """Unregister a skill by name."""
        if name in self._skills:
            del self._skills[name]
            return True
        return False
    
    def get(self, name: str) -> Optional[tuple[SkillMetadata, Callable]]:
        """Return the registered skill metadata and handler for a name."""
        return self._skills.get(name)
    
    def snapshot(self) -> list[dict]:
        """Return a metadata snapshot used by the prompt builder."""
        return [
            {
                "name": meta.name,
                "description": meta.description,
                "category": meta.category,
                "location": meta.location,
            }
            for meta, _ in self._skills.values()
        ]

    def snapshot_builtins(self) -> list[dict]:
        """Return a snapshot of built-in skills only, excluding tools derived from MD skills.

        MD-skill-derived tools are already exposed via md_snapshot() and shown in the
        MD Skills section of the system prompt / /skills API.  Including them again in
        the executable-skills listing causes every such skill to appear twice.
        """
        md_derived: set[str] = set()
        for tool_names in self._md_skill_tools.values():
            md_derived.update(tool_names)
        return [
            {
                "name": meta.name,
                "description": meta.description,
                "category": meta.category,
                "location": meta.location,
            }
            for meta, _ in self._skills.values()
            if meta.name not in md_derived
        ]
    
    def to_tool_definitions(self) -> list[dict]:
        """Convert registered skills into tool-definition dictionaries."""
        definitions = []
        for meta, handler in self._skills.values():
            schema = self._extract_schema(handler)
            definitions.append({
                "name": meta.name,
                "description": meta.description,
                "parameters": schema,
            })
        return definitions
    
    def register_to_agent(self, agent: Any) -> None:
        """
convert Skills register PydanticAI Agent tool
 
 Args:
 agent:PydanticAI Agent instance
 
"""
        for name, (meta, handler) in self._skills.items():
            # PydanticAI support tool
            if hasattr(agent, "tool"):
                # Inject RunContext and SkillDeps into handler's module globals if needed
                # This resolves forward reference type hints like "RunContext[SkillDeps]"
                handler_module = inspect.getmodule(handler)
                if handler_module:
                    if 'RunContext' not in handler_module.__dict__:
                        handler_module.__dict__['RunContext'] = RunContext
                    if 'SkillDeps' not in handler_module.__dict__:
                        handler_module.__dict__['SkillDeps'] = SkillDeps
                agent.tool(handler, name=name)
    
    async def execute(
        self,
        name: str,
        args_json: str,
        deps: Optional[Any] = None,
    ) -> str:
        """Execute a registered skill and return a JSON string result.

        This helper is used by workflow-style integrations and adapters that
        expect serialized JSON output rather than direct Python objects.
        """
        if name not in self._skills:
            return json.dumps({"error": f"Skill '{name}' not found"})
        
        meta, handler = self._skills[name]
        args = json.loads(args_json) if args_json else {}
        
        try:
            # check handler Run-Context parameter
            sig = inspect.signature(handler)
            params = list(sig.parameters.keys())
            
            if deps is not None and params and params[0] in ("ctx", "context"):
                # such as handler Run-Context, Mock context
                # use, deps
                from dataclasses import dataclass
                
                @dataclass
                class MockRunContext:
                    deps: Any
                
                ctx = MockRunContext(deps=deps)
                result = await handler(ctx, **args)
            else:
                result = await handler(**args)
            
            if isinstance(result, BaseModel):
                return result.model_dump_json()
            return json.dumps(result) if not isinstance(result, str) else result
            
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _extract_schema(self, handler: Callable) -> dict:
        """

from count JSON Schema
        
        Args:
            handler:handle count
            
        Returns:
            JSON Schema dictionary
        
"""
        sig = inspect.signature(handler)
        properties = {}
        required = []
        
        for name, param in sig.parameters.items():
            # ctx/context parameter
            if name in ("ctx", "context", "self"):
                continue
            
            # get type
            annotation = param.annotation
            param_type = "string"  # default
            
            if annotation != inspect.Parameter.empty:
                if annotation == int:
                    param_type = "integer"
                elif annotation == float:
                    param_type = "number"
                elif annotation == bool:
                    param_type = "boolean"
                elif annotation == list:
                    param_type = "array"
                elif annotation == dict:
                    param_type = "object"
            
            properties[name] = {"type": param_type}
            
            # check required
            if param.default == inspect.Parameter.empty:
                required.append(name)
        
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    
    def load_from_directory(
        self,
        directory: str,
        location: str = "built-in",
        *,
        provider: Optional[str] = None,
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    ) -> int:
        """Load skills from SKILL.md metadata in the target directory.

        Registration source is Markdown skill metadata only. Executable handlers
        are discovered via explicit entrypoint metadata in SKILL.md.
        """
        path = Path(directory).expanduser()
        if not path.exists():
            return 0

        return self._load_md_skills(
            path,
            location,
            provider=provider,
            max_file_bytes=max_file_bytes,
        )
    # ------------------------------------------------------------------
    # MD Skills
    # ------------------------------------------------------------------

    def _load_md_skills(
        self,
        base_path: Path,
        location: str,
        *,
        provider: Optional[str],
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    ) -> int:
        """

MD Skills.

        mode:
        - ``*/SKILL.md`` directory structure
        -``*.md``(exclude``_``prefix)

        Args:
            base_path:
            location:source identifier
            max_file_bytes:

        Returns:
            MD Skills count
        
"""
        count = 0

        # 1. directory structure:*/SKILL.md
        for skill_file in base_path.glob("*/SKILL.md"):
            if self._try_load_md_skill(
                skill_file,
                location,
                is_directory_skill=True,
                provider=provider,
                max_file_bytes=max_file_bytes,
            ):
                count += 1

        # 2.:*.md(exclude _ prefix)
        for md_file in base_path.glob("*.md"):
            if md_file.name.startswith("_"):
                continue
            if self._try_load_md_skill(
                md_file,
                location,
                is_directory_skill=False,
                provider=provider,
                max_file_bytes=max_file_bytes,
            ):
                count += 1

        return count

    def _try_load_md_skill(
        self,
        file_path: Path,
        location: str,
        *,
        is_directory_skill: bool,
        provider: Optional[str],
        max_file_bytes: int,
    ) -> bool:
        """

single MD Skill.

        Returns:
            
        
"""
        # check
        try:
            file_size = file_path.stat().st_size
        except OSError as e:
            logger.warning("Cannot stat %s: %s", file_path, e)
            return False

        if file_size > max_file_bytes:
            logger.warning(
                "Skipping %s: file size %d exceeds limit %d",
                file_path,
                file_size,
                max_file_bytes,
            )
            return False

        # parse Frontmatter
        try:
            raw = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read %s: %s", file_path, e)
            return False

        fm = parse_frontmatter(raw)

        # nameparse:Frontmatter name > / stem
        if is_directory_skill:
            parent_dir_name = file_path.parent.name
            name = fm.metadata.get("name", parent_dir_name)
        else:
            name = fm.metadata.get("name", file_path.stem)

        # name
        parent_check = file_path.parent.name if is_directory_skill else None
        err = validate_skill_name(name, parent_dir_name=parent_check)
        if err:
            logger.warning("Skipping %s: invalid name '%s' - %s", file_path, name, err)
            return False

        # description
        description = fm.metadata.get("description", "").strip()
        if not description:
            logger.warning("Skipping %s: missing or empty description", file_path)
            return False

        if len(description) > _MAX_DESCRIPTION_LENGTH:
            logger.warning(
                "Skipping %s: description length %d exceeds %d",
                file_path,
                len(description),
                _MAX_DESCRIPTION_LENGTH,
            )
            return False

        # metadata(name/description)
        metadata = {
            k: v
            for k, v in fm.metadata.items()
            if k not in ("name", "description")
        }

        provider_name = str(metadata.get("provider_type", "")).strip() or (provider or "").strip()
        qualified_name = f"{provider_name}:{name}" if provider_name else name

        # :location >=
        if qualified_name in self._md_skills:
            existing = self._md_skills[qualified_name]
            if not self._should_override(existing.location, location):
                return False
            self._unregister_md_skill_tools(existing.qualified_name)

        entry = MdSkillEntry(
            name=name,
            description=description,
            file_path=str(file_path.resolve()),
            provider=provider_name,
            qualified_name=qualified_name,
            location=location,
            metadata=metadata,
        )
        self._md_skills[qualified_name] = entry
        self._register_executable_tools_from_md(entry)
        return True

    def _unregister_md_skill_tools(self, qualified_name: str) -> None:
        tool_names = self._md_skill_tools.pop(qualified_name, set())
        for tool_name in tool_names:
            self.unregister(tool_name)

    def _register_executable_tools_from_md(self, entry: MdSkillEntry) -> None:
        skill_dir = Path(entry.file_path).parent
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}

        registered: set[str] = set()

        single_tool_name = str(metadata.get("tool_name", "")).strip()
        single_entrypoint = str(metadata.get("entrypoint", "")).strip()
        if single_tool_name and single_entrypoint:
            self._register_md_tool_entry(
                tool_name=single_tool_name,
                entrypoint=single_entrypoint,
                entry=entry,
                skill_dir=skill_dir,
                registered=registered,
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
            self._register_md_tool_entry(
                tool_name=tool_name,
                entrypoint=entrypoint,
                tool_description=tool_description,
                entry=entry,
                skill_dir=skill_dir,
                registered=registered,
            )

        if registered:
            self._md_skill_tools[entry.qualified_name] = registered

    def _register_md_tool_entry(
        self,
        *,
        tool_name: str,
        entrypoint: str,
        tool_description: str = "",
        entry: MdSkillEntry,
        skill_dir: Path,
        registered: set[str],
    ) -> None:
        module_path, attr_name = self._parse_entrypoint(entrypoint)
        py_file = (skill_dir / module_path).resolve()
        if not py_file.is_file():
            logger.warning(
                "Skipping md tool %s from %s: entrypoint file not found: %s",
                tool_name,
                entry.name,
                py_file,
            )
            return

        # Get provider_type from skill metadata
        provider_type = (str(entry.metadata.get("provider_type", "")).strip() or entry.provider or None)
        
        try:
            handler = self._load_handler_from_file(py_file, attr_name, provider_type)
        except Exception as exc:
            logger.warning(
                "Skipping md tool %s from %s: failed loading handler %s (%s)",
                tool_name,
                entry.name,
                entrypoint,
                exc,
            )
            return

        # Use tool_description if provided, otherwise fall back to entry description
        description = tool_description if tool_description else entry.description
        
        meta = SkillMetadata(
            name=tool_name,
            description=description,
            category=str(entry.metadata.get("category", "skill")),
            location=entry.location,
            provider_type=provider_type,
            instance_required=str(entry.metadata.get("instance_required", "")).lower() in ("1", "true", "yes"),
        )
        self.register(meta, handler)
        registered.add(tool_name)

    @staticmethod
    def _parse_entrypoint(entrypoint: str) -> tuple[str, str]:
        if ":" in entrypoint:
            module_path, attr_name = entrypoint.rsplit(":", 1)
            return module_path.strip(), attr_name.strip() or "handler"
        return entrypoint.strip(), "handler"

    @staticmethod
    def _load_handler_from_file(py_file: Path, attr_name: str, provider_type: Optional[str] = None) -> Callable:
        import sys

        scripts_dir = str(py_file.parent)
        inserted = False
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
            inserted = True

        try:
            # If attr_name is the default "handler", assume it's a script wrapper case
            # Don't try to load the module, just create a wrapper
            if attr_name == "handler":
                return SkillRegistry._create_script_wrapper(py_file, provider_type)
            
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
            
            # If no callable found, create a script wrapper
            return SkillRegistry._create_script_wrapper(py_file, provider_type)
        finally:
            if inserted:
                try:
                    sys.path.remove(scripts_dir)
                except ValueError:
                    pass
    
    @staticmethod
    def _create_script_wrapper(py_file: Path, provider_type: Optional[str] = None) -> Callable:
        """Create a wrapper function that executes a script file.
        
        This allows any script (Python, Bash, etc.) to be used as a skill tool.
        The script will be executed with environment variables from the selected
        provider instance if available.
        
        Args:
            py_file: Path to the script file
            provider_type: Optional provider type (e.g., 'smartcmp') to select the right instance
        """
        import subprocess
        import sys
        import os
        
        async def script_handler(ctx=None, **kwargs) -> dict:
            """Execute the script and return results.
            
            Args:
                ctx: Optional RunContext (passed by pydantic-ai)
                **kwargs: Additional arguments
            """
            # Build environment variables
            env = os.environ.copy()

            # NEW: Inject all cookies from ctx.deps if available
            if ctx is not None and hasattr(ctx, 'deps') and hasattr(ctx.deps, 'cookies'):
                cookies = ctx.deps.cookies
                if cookies:
                    try:
                        env['ATLASCLAW_COOKIES'] = json.dumps(cookies)
                        if hasattr(ctx.deps, 'user_info') and ctx.deps.user_info:
                            print(f"[DEBUG] Set ATLASCLAW_COOKIES for user={ctx.deps.user_info.user_id}, cookies={list(cookies.keys())}")
                    except (TypeError, ValueError) as e:
                        print(f"[WARNING] Failed to serialize cookies: {e}")

            # NEW: Inject provider config from ctx.deps.extra if available
            if ctx is not None and hasattr(ctx, 'deps') and hasattr(ctx.deps, 'extra'):
                extra = ctx.deps.extra
                provider_config = extra.get('provider_config', {}) if extra else {}
                if provider_config:
                    try:
                        env['ATLASCLAW_PROVIDER_CONFIG'] = json.dumps(provider_config)
                        print(f"[DEBUG] Set ATLASCLAW_PROVIDER_CONFIG with providers: {list(provider_config.keys())}")
                    except (TypeError, ValueError) as e:
                        print(f"[WARNING] Failed to serialize provider_config: {e}")

            # Inject provider instance configuration from ctx.deps.extra if available
            if ctx is not None and hasattr(ctx, 'deps') and hasattr(ctx.deps, 'extra'):
                extra = ctx.deps.extra
                
                # Debug: Print all deps info
                print(f"[DEBUG] Tool execution: provider_type={provider_type}")
                print(f"[DEBUG] ctx.deps.extra keys: {list(extra.keys())}")
                
                # Check for selected provider instance
                provider_instance = extra.get('provider_instance')
                if provider_instance:
                    print(f"[DEBUG] Using selected provider_instance: {provider_instance}")
                    # Inject all provider instance config as environment variables
                    for key, value in provider_instance.items():
                        if value is not None and key not in ('password', 'token', 'secret'):
                            env[key.upper()] = str(value)
                        elif value is not None and key in ('cookie',):
                            # For cookie, set as-is
                            env[key.upper()] = str(value)
                # Check provider_instances for matching provider_type
                elif 'provider_instances' in extra:
                    provider_instances = extra['provider_instances']
                    print(f"[DEBUG] Available provider_types: {list(provider_instances.keys())}")
                    
                    # If tool has a specific provider_type, use it; otherwise find first available
                    target_provider = provider_type
                    if target_provider and target_provider in provider_instances:
                        instances = provider_instances[target_provider]
                        print(f"[DEBUG] Found instances for {target_provider}: {list(instances.keys())}")
                        if instances:
                            default_instance = list(instances.values())[0]
                            print(f"[DEBUG] Using instance config: {list(default_instance.keys())}")
                            for key, value in default_instance.items():
                                if value is not None and key not in ('password', 'token', 'secret'):
                                    env[key.upper()] = str(value)
                                    print(f"[DEBUG] Set env var: {key.upper()}={str(value)[:50]}...")
                                elif value is not None and key in ('cookie',):
                                    env[key.upper()] = str(value)
                                    print(f"[DEBUG] Set env var: {key.upper()}={str(value)[:50]}...")
                    else:
                        # Fall back to first available provider instance
                        print(f"[DEBUG] No specific provider_type, using first available")
                        for pt, instances in provider_instances.items():
                            if instances:
                                default_instance = list(instances.values())[0]
                                for key, value in default_instance.items():
                                    if value is not None and key not in ('password', 'token', 'secret'):
                                        env[key.upper()] = str(value)
                                    elif value is not None and key in ('cookie',):
                                        env[key.upper()] = str(value)
                                break
            
            # Add any kwargs as environment variables
            for key, value in kwargs.items():
                if value is not None:
                    env[key.upper()] = str(value)
            
            # Determine how to execute based on file extension
            if py_file.suffix == '.py':
                cmd = [sys.executable, str(py_file)]
            elif py_file.suffix in ['.sh', '.bash']:
                cmd = ['bash', str(py_file)]
            elif py_file.suffix == '.ps1':
                cmd = ['powershell', '-File', str(py_file)]
            else:
                # Try to execute directly
                cmd = [str(py_file)]
            
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                    cwd=str(py_file.parent)
                )
                
                output = result.stdout
                if result.stderr:
                    output += f"\n[STDERR] {result.stderr}"
                
                return {
                    "success": result.returncode == 0,
                    "returncode": result.returncode,
                    "output": output
                }
            except subprocess.TimeoutExpired:
                return {"success": False, "error": "Script execution timed out"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        return script_handler
    @staticmethod
    def _should_override(existing_location: str, new_location: str) -> bool:
        """

location entry.

        :workspace > user > built-in
        
"""
        priority = {"built-in": 0, "external": 1, "user": 2, "workspace": 3}
        return priority.get(new_location, 0) >= priority.get(existing_location, 0)

    # ------------------------------------------------------------------
    # MD Skills
    # ------------------------------------------------------------------

    def md_snapshot(self) -> list[dict]:
        """


return MD Skills metadatasnapshot.

 used for Request-Orchestrator inject deps_extra and Prompt-Builder.

 Returns:
 MD Skills metadatalist
 
"""
        return [
            {
                "name": entry.name,
                "provider": entry.provider,
                "qualified_name": entry.qualified_name,
                "description": entry.description,
                "file_path": entry.file_path,
                "location": entry.location,
                "metadata": dict(entry.metadata),
            }
            for entry in self._md_skills.values()
        ]

    def list_md_skills(self) -> list[str]:
        """return MD Skill namelist."""
        return [entry.name for entry in self._md_skills.values()]

    def list_md_qualified_skills(self) -> list[str]:
        """Return all provider-qualified markdown skill identifiers."""
        return list(self._md_skills.keys())

    def get_md_skill(self, identifier: str) -> Optional[MdSkillEntry]:
        """Resolve a markdown skill by qualified name or, when unique, bare name."""
        if identifier in self._md_skills:
            return self._md_skills[identifier]

        matches = [entry for entry in self._md_skills.values() if entry.name == identifier]
        if len(matches) == 1:
            return matches[0]
        return None
    
    def list_skills(self) -> list[str]:
        """

register name
        
        Returns:
            namelist
        
"""
        return list(self._skills.keys())



