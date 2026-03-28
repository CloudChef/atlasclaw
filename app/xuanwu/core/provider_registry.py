"""Service provider registry for provider templates, instances, and skill wrappers."""

from __future__ import annotations

import functools
import importlib.util
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps
    from app.xuanwu.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")

_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "private_key",
        "credential",
    }
)


def _resolve_env(value: str) -> str:
    """Resolve ${VAR} or ${VAR:default} placeholders."""

    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")

    return _ENV_PATTERN.sub(_replacer, value)


def _resolve_env_recursive(obj: Any) -> Any:
    """Resolve environment placeholders in nested structures."""
    if isinstance(obj, str):
        return _resolve_env(obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_recursive(item) for item in obj]
    return obj


def _is_sensitive(key: str) -> bool:
    return key.lower() in _SENSITIVE_KEYS


def _redact_config(config: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if _is_sensitive(k) else v) for k, v in config.items()}


@dataclass
class ProviderTemplate:
    name: str
    path: Path
    md_path: Path
    skills_dir: Path


@dataclass
class ProviderContext:
    """LLM context information for a provider.
    
    Used by PromptBuilder to generate rich skill selection context.
    """
    provider_type: str
    display_name: str = ""
    version: str = ""
    keywords: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    use_when: list[str] = field(default_factory=list)
    avoid_when: list[str] = field(default_factory=list)
    description: str = ""  # First paragraph of PROVIDER.md body


class ServiceProviderRegistry:
    """Registry of provider templates and provider instances from configuration."""

    def __init__(self) -> None:
        self._templates: dict[str, ProviderTemplate] = {}
        self._instances: dict[str, dict[str, dict[str, Any]]] = {}
        self._contexts: dict[str, ProviderContext] = {}  # LLM context for each provider

    def load_from_directory(self, providers_dir: Path) -> int:
        """Load provider templates from a providers directory."""
        providers_dir = Path(providers_dir).expanduser()
        if not providers_dir.is_dir():
            logger.debug("providers directory does not exist: %s", providers_dir)
            return 0

        count = 0
        for sub in sorted(providers_dir.iterdir()):
            if not sub.is_dir() or sub.name.startswith(("_", ".")):
                continue

            md_path = self._find_provider_md(sub)
            if md_path is None:
                logger.warning(
                    "Skipping provider directory %s: missing PROVIDER.md or %s.md",
                    sub.name,
                    sub.name,
                )
                continue

            template = ProviderTemplate(
                name=sub.name,
                path=sub,
                md_path=md_path,
                skills_dir=sub / "skills",
            )
            self._templates[sub.name] = template
            
            # Parse PROVIDER.md frontmatter for LLM context
            context = self._parse_provider_context(md_path, sub.name)
            if context:
                self._contexts[context.provider_type] = context
                logger.debug(
                    "Loaded provider context: %s (keywords=%d, capabilities=%d)",
                    context.provider_type,
                    len(context.keywords),
                    len(context.capabilities),
                )
            
            count += 1
            logger.info("Discovered provider: %s (%s)", sub.name, md_path.name)

        return count

    def _parse_provider_context(self, md_path: Path, fallback_name: str) -> Optional[ProviderContext]:
        """Parse PROVIDER.md frontmatter to extract LLM context.
        
        Args:
            md_path: Path to PROVIDER.md file
            fallback_name: Provider directory name to use if provider_type not in frontmatter
            
        Returns:
            ProviderContext or None if parsing fails
        """
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read PROVIDER.md %s: %s", md_path, e)
            return None
        
        from app.xuanwu.skills.frontmatter import parse_frontmatter
        
        result = parse_frontmatter(content)
        meta = result.metadata
        
        # Extract provider_type, fallback to directory name
        provider_type = meta.get("provider_type", fallback_name)
        if isinstance(provider_type, list):
            provider_type = provider_type[0] if provider_type else fallback_name
        
        # Extract first paragraph of body as description
        description = ""
        body = result.body.strip()
        if body:
            # Skip the title line (starts with #)
            lines = body.split("\n")
            para_lines = []
            started = False
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    if started:
                        break
                    continue
                if stripped.startswith("#"):
                    continue
                started = True
                para_lines.append(stripped)
            description = " ".join(para_lines)
        
        # Helper to ensure list type
        def as_list(val: Any) -> list[str]:
            if isinstance(val, list):
                return [str(v) for v in val]
            if isinstance(val, str) and val:
                return [val]
            return []
        
        return ProviderContext(
            provider_type=str(provider_type),
            display_name=str(meta.get("display_name", "")),
            version=str(meta.get("version", "")),
            keywords=as_list(meta.get("keywords")),
            capabilities=as_list(meta.get("capabilities")),
            use_when=as_list(meta.get("use_when")),
            avoid_when=as_list(meta.get("avoid_when")),
            description=description,
        )

    def get_provider_context(self, provider_type: str) -> Optional[ProviderContext]:
        """Get LLM context for a provider.
        
        Args:
            provider_type: Provider type identifier
            
        Returns:
            ProviderContext or None if not found
        """
        return self._contexts.get(provider_type)

    def get_all_provider_contexts(self) -> dict[str, ProviderContext]:
        """Get all provider contexts.
        
        Returns:
            Dictionary mapping provider_type to ProviderContext
        """
        return dict(self._contexts)

    def load_instances_from_config(self, config: dict[str, dict[str, Any]]) -> None:
        """Load provider instance configuration from xuanwu config."""
        for provider_type, instances in config.items():
            if not isinstance(instances, dict):
                logger.warning(
                    "Provider %s config format invalid: expected dict, got %s",
                    provider_type,
                    type(instances).__name__,
                )
                continue

            resolved_instances: dict[str, dict[str, Any]] = {}
            for instance_name, params in instances.items():
                if not isinstance(params, dict):
                    logger.warning(
                        "Provider %s.%s config format invalid: expected dict, got %s",
                        provider_type,
                        instance_name,
                        type(params).__name__,
                    )
                    continue
                resolved_instances[instance_name] = _resolve_env_recursive(params)

            self._instances[provider_type] = resolved_instances
            logger.info(
                "Loaded provider instances: %s -> %s",
                provider_type,
                list(resolved_instances.keys()),
            )

    def list_providers(self) -> list[str]:
        all_types = set(self._templates.keys()) | set(self._instances.keys())
        return sorted(all_types)

    def list_instances(self, provider_type: str) -> list[str]:
        return sorted(self._instances.get(provider_type, {}).keys())

    def get_instance_config(
        self,
        provider_type: str,
        instance_name: str,
    ) -> Optional[dict[str, Any]]:
        instances = self._instances.get(provider_type)
        if instances is None:
            return None
        config = instances.get(instance_name)
        return dict(config) if isinstance(config, dict) else None

    def get_instance_config_redacted(
        self,
        provider_type: str,
        instance_name: str,
    ) -> Optional[dict[str, Any]]:
        config = self.get_instance_config(provider_type, instance_name)
        if config is None:
            return None
        return _redact_config(config)

    def get_all_instance_configs(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return all resolved provider instance configs."""
        return {
            provider_type: {
                instance_name: dict(instance_cfg)
                for instance_name, instance_cfg in instances.items()
            }
            for provider_type, instances in self._instances.items()
        }

    def get_available_providers_summary(self) -> dict[str, list[str]]:
        return {
            provider_type: self.list_instances(provider_type)
            for provider_type in self.list_providers()
        }

    def get_template(self, provider_type: str) -> Optional[ProviderTemplate]:
        return self._templates.get(provider_type)

    def register_skills_to(self, skill_registry: "SkillRegistry") -> int:
        """Load and register provider skills with provider-aware wrappers."""
        from app.xuanwu.skills.registry import SkillMetadata

        total = 0
        for provider_type, template in self._templates.items():
            if not template.skills_dir.is_dir():
                continue

            for py_file in sorted(template.skills_dir.glob("*.py")):
                if py_file.name.startswith("_") or py_file.stem == "shared":
                    continue

                try:
                    module = self._load_module(py_file)
                except Exception as exc:
                    logger.warning("Failed loading provider skill %s: %s", py_file, exc)
                    continue

                skill_metadata_raw = getattr(module, "SKILL_METADATA", None)
                handler = getattr(module, "handler", None)
                if skill_metadata_raw is None or handler is None:
                    continue

                original_name = (
                    skill_metadata_raw.name if hasattr(skill_metadata_raw, "name") else py_file.stem
                )
                description = (
                    skill_metadata_raw.description
                    if hasattr(skill_metadata_raw, "description")
                    else ""
                )
                prefixed_name = f"{provider_type}__{original_name}"

                metadata = SkillMetadata(
                    name=prefixed_name,
                    description=description,
                    category=f"provider:{provider_type}",
                    provider_type=provider_type,
                    instance_required=True,
                    location="built-in",
                )

                wrapped = self._make_handler_wrapper(handler=handler, provider_type=provider_type)
                skill_registry.register(metadata, wrapped)
                total += 1

        logger.info("Registered %d provider skills", total)
        return total

    def _make_handler_wrapper(self, handler: Callable, provider_type: str) -> Callable:
        registry = self

        @functools.wraps(handler)
        async def wrapper(ctx: "RunContext[SkillDeps]", **kwargs: Any) -> Any:
            extra = (
                ctx.deps.extra
                if hasattr(ctx, "deps") and isinstance(getattr(ctx.deps, "extra", None), dict)
                else {}
            )

            # Always expose all provider instances to skills via deps.extra.
            extra.setdefault("provider_instances", registry.get_all_instance_configs())

            selected_type = str(extra.get("provider_type", ""))
            selected_name = str(extra.get("provider_instance_name", ""))
            selected_cfg = extra.get("provider_instance")

            # If already selected and valid for this provider, run directly.
            if (
                selected_type == provider_type
                and selected_name
                and isinstance(selected_cfg, dict)
            ):
                return await handler(ctx, **kwargs)

            instances = registry.list_instances(provider_type)
            if len(instances) == 0:
                return {
                    "is_error": True,
                    "content": [
                        {"type": "text", "text": f"Provider '{provider_type}' has no configured instances."}
                    ],
                }

            # Keep an explicitly chosen instance name if present.
            if selected_type == provider_type and selected_name in instances:
                cfg = registry.get_instance_config(provider_type, selected_name) or {}
                extra["provider_type"] = provider_type
                extra["provider_instance_name"] = selected_name
                extra["provider_instance"] = cfg
                return await handler(ctx, **kwargs)

            # Auto-select if exactly one instance exists.
            if len(instances) == 1:
                instance_name = instances[0]
                cfg = registry.get_instance_config(provider_type, instance_name) or {}
                extra["provider_type"] = provider_type
                extra["provider_instance_name"] = instance_name
                extra["provider_instance"] = cfg
                return await handler(ctx, **kwargs)

            # Multiple instances and no selection.
            return {
                "is_error": True,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Provider '{provider_type}' has {len(instances)} instances: "
                            f"{', '.join(instances)}. Call list_provider_instances('{provider_type}') "
                            "then select_provider_instance before invoking provider skills."
                        ),
                    }
                ],
            }

        return wrapper

    @staticmethod
    def _find_provider_md(directory: Path) -> Optional[Path]:
        provider_md = directory / "PROVIDER.md"
        if provider_md.is_file():
            return provider_md

        named_md = directory / f"{directory.name}.md"
        if named_md.is_file():
            return named_md

        return None

    @staticmethod
    def _load_module(file_path: Path) -> Any:
        spec = importlib.util.spec_from_file_location(f"provider_skill_{file_path.stem}", file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module: {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module