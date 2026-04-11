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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from app.atlasclaw.skills.frontmatter import parse_frontmatter
from app.atlasclaw.skills.md_tool_runtime import (
    register_executable_tools_from_md,
    should_override_location,
)
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.tools.catalog import GROUP_TOOLS

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
    source: str = "builtin"
    group_ids: list[str] = Field(default_factory=list)
    capability_class: str = ""
    priority: int = 100
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    planner_visibility: str = "contextual"
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    result_mode: str = "llm"


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
    
    def __init__(
        self,
        workspace: Optional[str] = None,
        *,
        allow_script_execution: bool = False,
    ):
        """Initialize the registry."""
        self._skills: dict[str, tuple[SkillMetadata, Callable]] = {}
        self._md_skills: dict[str, MdSkillEntry] = {}
        self._md_skill_tools: dict[str, set[str]] = {}
        self._md_tool_profiles: dict[str, dict[str, Any]] = {}
        self._workspace = workspace
        self._allow_script_execution = allow_script_execution
    
    def register(
        self,
        metadata: SkillMetadata,
        handler: Callable,
    ) -> None:
        """Register an executable skill handler."""
        self._skills[metadata.name] = (metadata, handler)
        if metadata.source in {"md_skill", "provider"}:
            self._md_tool_profiles[metadata.name] = {
                "source": metadata.source,
                "provider_type": metadata.provider_type or "",
                "group_ids": list(metadata.group_ids or []),
                "capability_class": metadata.capability_class or "",
                "priority": int(metadata.priority or 100),
            }

    def unregister(self, name: str) -> bool:
        """Unregister a skill by name."""
        if name in self._skills:
            del self._skills[name]
            self._md_tool_profiles.pop(name, None)
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
                "provider_type": meta.provider_type,
                "instance_required": meta.instance_required,
                "source": meta.source,
                "group_ids": list(meta.group_ids),
                "capability_class": meta.capability_class,
                "priority": meta.priority,
                "parameters_schema": self._coerce_parameters_schema(meta.parameters_schema),
                "planner_visibility": str(meta.planner_visibility or "").strip(),
                "aliases": list(meta.aliases),
                "keywords": list(meta.keywords),
                "use_when": list(meta.use_when),
                "avoid_when": list(meta.avoid_when),
                "result_mode": str(meta.result_mode or "").strip(),
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
                "provider_type": meta.provider_type,
                "instance_required": meta.instance_required,
                "source": meta.source,
                "group_ids": list(meta.group_ids),
                "capability_class": meta.capability_class,
                "priority": meta.priority,
                "parameters_schema": self._coerce_parameters_schema(meta.parameters_schema),
                "planner_visibility": str(meta.planner_visibility or "").strip(),
                "aliases": list(meta.aliases),
                "keywords": list(meta.keywords),
                "use_when": list(meta.use_when),
                "avoid_when": list(meta.avoid_when),
                "result_mode": str(meta.result_mode or "").strip(),
            }
            for meta, _ in self._skills.values()
            if meta.name not in md_derived
        ]

    def tools_snapshot(self) -> list[dict]:
        """Return normalized executable-tool metadata for runtime filtering."""
        normalized: list[dict] = []
        for meta, _ in self._skills.values():
            source = self._resolve_tool_source(meta)
            provider_type = str(meta.provider_type or "").strip()
            capability_class = self._resolve_capability_class(meta, source)
            group_ids = self._resolve_group_ids(meta, source, provider_type)
            record = {
                "name": meta.name,
                "description": meta.description,
                "source": source,
                "provider_type": provider_type,
                "group_ids": group_ids,
                "capability_class": capability_class,
                "priority": int(meta.priority or 100),
                "category": meta.category,
                "location": meta.location,
                "parameters_schema": self._coerce_parameters_schema(meta.parameters_schema),
                "planner_visibility": str(meta.planner_visibility or "").strip(),
                "aliases": list(meta.aliases),
                "keywords": list(meta.keywords),
                "use_when": list(meta.use_when),
                "avoid_when": list(meta.avoid_when),
                "result_mode": str(meta.result_mode or "").strip(),
            }
            normalized.append(record)
        return normalized

    def tool_groups_snapshot(self) -> dict[str, list[str]]:
        """Return merged group->tool mappings for built-ins and provider/md tools."""
        available = {item["name"] for item in self.tools_snapshot()}
        merged: dict[str, set[str]] = {}

        for group_id, members in GROUP_TOOLS.items():
            for tool_name in members:
                if tool_name not in available:
                    continue
                merged.setdefault(group_id, set()).add(tool_name)

        for tool in self.tools_snapshot():
            tool_name = str(tool.get("name", "")).strip()
            if not tool_name:
                continue
            for group_id in tool.get("group_ids", []):
                normalized_group = str(group_id or "").strip()
                if not normalized_group:
                    continue
                merged.setdefault(normalized_group, set()).add(tool_name)

        return {key: sorted(values) for key, values in merged.items()}
    
    def to_tool_definitions(self) -> list[dict]:
        """Convert registered skills into tool-definition dictionaries."""
        definitions = []
        for meta, handler in self._skills.values():
            schema = self._coerce_parameters_schema(meta.parameters_schema) or self._extract_schema(handler)
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
            self.register_entry_to_agent(agent, meta, handler)

    def register_entry_to_agent(
        self,
        agent: Any,
        metadata: SkillMetadata,
        handler: Callable,
    ) -> None:
        """Register one executable skill on an agent using metadata-aware schema exposure."""
        if not hasattr(agent, "tool"):
            return

        handler_module = inspect.getmodule(handler)
        if handler_module:
            if "RunContext" not in handler_module.__dict__:
                handler_module.__dict__["RunContext"] = RunContext
            if "SkillDeps" not in handler_module.__dict__:
                handler_module.__dict__["SkillDeps"] = SkillDeps

        runtime_handler = self._build_runtime_handler(metadata, handler)
        agent.tool(runtime_handler, name=metadata.name)
    
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

    def _build_runtime_handler(
        self,
        metadata: SkillMetadata,
        handler: Callable,
    ) -> Callable:
        """Build a metadata-aware runtime handler so the model sees the intended tool schema."""
        parameters_schema = self._coerce_parameters_schema(metadata.parameters_schema)
        if not parameters_schema:
            return handler

        accepts_ctx = self._handler_accepts_ctx(handler)
        signature = self._build_runtime_signature(parameters_schema)
        docstring = self._build_runtime_docstring(metadata, parameters_schema)

        async def runtime_handler(ctx: RunContext[SkillDeps], **kwargs: Any) -> Any:
            if accepts_ctx:
                result = handler(ctx, **kwargs)
            else:
                result = handler(**kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        runtime_handler.__name__ = re.sub(r"[^a-zA-Z0-9_]", "_", metadata.name) or "atlasclaw_tool"
        runtime_handler.__qualname__ = runtime_handler.__name__
        runtime_handler.__doc__ = docstring
        runtime_handler.__signature__ = signature
        runtime_handler.__annotations__ = {
            parameter.name: parameter.annotation
            for parameter in signature.parameters.values()
            if parameter.annotation is not inspect.Parameter.empty
        }
        runtime_handler.__annotations__["return"] = Any
        return runtime_handler

    @staticmethod
    def _handler_accepts_ctx(handler: Callable) -> bool:
        """Return whether the original handler expects a RunContext-style first argument."""
        try:
            params = list(inspect.signature(handler).parameters.keys())
        except (TypeError, ValueError):
            return False
        if not params:
            return False
        return params[0] in {"ctx", "context"}

    @staticmethod
    def _coerce_parameters_schema(raw_schema: Any) -> dict[str, Any]:
        """Normalize optional JSON-schema metadata into a usable object schema."""
        if isinstance(raw_schema, str):
            payload = raw_schema.strip()
            if not payload:
                return {}
            try:
                raw_schema = json.loads(payload)
            except json.JSONDecodeError:
                return {}
        if not isinstance(raw_schema, dict):
            return {}
        schema_type = str(raw_schema.get("type", "") or "").strip().lower()
        if schema_type and schema_type != "object":
            return {}
        properties = raw_schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            return {}
        normalized: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        required = raw_schema.get("required")
        if isinstance(required, list):
            normalized["required"] = [str(item) for item in required if str(item).strip()]
        return normalized

    def _build_runtime_signature(self, parameters_schema: dict[str, Any]) -> inspect.Signature:
        """Build an explicit function signature from metadata JSON schema."""
        parameters: list[inspect.Parameter] = [
            inspect.Parameter(
                "ctx",
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=RunContext[SkillDeps],
            )
        ]

        properties = parameters_schema.get("properties", {})
        required_names = {
            str(item).strip()
            for item in parameters_schema.get("required", []) or []
            if str(item).strip()
        }
        for name, schema in properties.items():
            normalized_name = str(name).strip()
            if not normalized_name:
                continue
            property_schema = schema if isinstance(schema, dict) else {}
            default = property_schema.get("default", inspect.Parameter.empty)
            if normalized_name not in required_names and default is inspect.Parameter.empty:
                default = None
            parameters.append(
                inspect.Parameter(
                    normalized_name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=self._schema_to_annotation(property_schema),
                )
            )
        return inspect.Signature(parameters=parameters)

    @staticmethod
    def _schema_to_annotation(property_schema: dict[str, Any]) -> Any:
        """Map a JSON-schema property to a reasonable Python annotation."""
        schema_type = str(property_schema.get("type", "") or "").strip().lower()
        if schema_type == "string":
            return str
        if schema_type == "integer":
            return int
        if schema_type == "number":
            return float
        if schema_type == "boolean":
            return bool
        if schema_type == "array":
            return list[Any]
        if schema_type == "object":
            return dict[str, Any]
        return Any

    @staticmethod
    def _build_runtime_docstring(
        metadata: SkillMetadata,
        parameters_schema: dict[str, Any],
    ) -> str:
        """Generate a compact docstring with parameter descriptions for tool exposure."""
        lines = [metadata.description.strip() or f"Execute {metadata.name}."]
        properties = parameters_schema.get("properties", {})
        if not isinstance(properties, dict) or not properties:
            return "\n".join(lines).strip()
        required_names = {
            str(item).strip()
            for item in parameters_schema.get("required", []) or []
            if str(item).strip()
        }
        lines.append("")
        lines.append("Args:")
        for name, schema in properties.items():
            property_schema = schema if isinstance(schema, dict) else {}
            description = str(property_schema.get("description", "") or "").strip()
            type_name = str(property_schema.get("type", "value") or "value").strip()
            requirement = "required" if str(name).strip() in required_names else "optional"
            summary = description or f"{type_name} parameter"
            lines.append(f"    {name}: {summary} ({requirement}).")
        return "\n".join(lines).strip()
    
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
        register_executable_tools_from_md(
            registry=self,
            entry=entry,
            skill_metadata_cls=SkillMetadata,
            logger=logger,
            allow_script_execution=self._allow_script_execution,
        )

    @staticmethod
    def _should_override(existing_location: str, new_location: str) -> bool:
        return should_override_location(existing_location, new_location)

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

    def _resolve_tool_source(self, meta: SkillMetadata) -> str:
        explicit_source = str(meta.source or "").strip().lower()
        if explicit_source in {"builtin", "provider", "md_skill"}:
            return explicit_source

        category = str(meta.category or "").strip().lower()
        if category.startswith("builtin:"):
            return "builtin"
        if str(meta.provider_type or "").strip():
            return "provider"
        if meta.name in self._md_tool_profiles:
            profile_source = str(self._md_tool_profiles[meta.name].get("source", "")).strip()
            if profile_source in {"builtin", "provider", "md_skill"}:
                return profile_source
        return "md_skill"

    def _resolve_capability_class(self, meta: SkillMetadata, source: str) -> str:
        explicit = str(meta.capability_class or "").strip()
        if explicit:
            return explicit

        provider_type = str(meta.provider_type or "").strip()
        if provider_type:
            return f"provider:{provider_type}"

        name = str(meta.name or "").strip().lower()
        if name in {"web_search", "web_fetch"}:
            return name
        if name == "browser":
            return "browser"
        if name == "openmeteo_weather":
            return "weather"
        if source == "md_skill":
            return "skill"
        return ""

    def _resolve_group_ids(
        self,
        meta: SkillMetadata,
        source: str,
        provider_type: str,
    ) -> list[str]:
        group_ids = [str(item).strip() for item in (meta.group_ids or []) if str(item).strip()]
        category = str(meta.category or "").strip().lower()
        if category.startswith("builtin:"):
            group_name = category.split(":", 1)[1].strip()
            if group_name:
                group_ids.append(f"group:{group_name}")
        if provider_type:
            group_ids.append(f"group:{provider_type}")
        if source == "builtin":
            group_ids.append("group:atlasclaw")

        deduped: list[str] = []
        seen: set[str] = set()
        for item in group_ids:
            normalized = item if item.startswith("group:") else f"group:{item}"
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped


