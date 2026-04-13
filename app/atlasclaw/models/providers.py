"""

LLM modelprovider

implementmulti LLM, through base_url + api_key + model:
- ProviderConfig:single Provider configuration
- ProviderPreset:Built-in provider preset
- Provider-Registry:Provider registry(+ userconfiguration)
- ModelFactory:Model factory(provider/model -> PydanticAI Model instance)
- resolve_env():environment variableparse(${VAR} / ${VAR:default})
"""

from __future__ import annotations

import os
import re
import logging
from typing import Optional, Any

from pydantic import BaseModel, Field

from app.atlasclaw.core.trace import create_traced_http_client

logger = logging.getLogger(__name__)


# ============================================================
# environment variableparse
# ============================================================

_ENV_PATTERN = re.compile(r'\$\{(\w+)(?::([^}]*))?\}')


def resolve_env(value: str) -> str:
    """

parse ${VAR_NAME} or ${VAR_NAME:default} for matenvironment variable

    Args:
        value:contains ${VAR} characters

    Returns:
        parse characters
    
"""
    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")

    return _ENV_PATTERN.sub(_replacer, value)


def mask_api_key(key: str) -> str:
    """

API Key, 3

    Args:
        key:raw API Key

    Returns:
        characters, such as sk-***abc
    
"""
    if not key or len(key) <= 3:
        return "***"
    return f"{key[:3]}***{key[-3:]}"


# ============================================================
# configurationmodel
# ============================================================

class ModelDefinition(BaseModel):
    """


modelmetadata(optional)

 Attributes:
 id:model ID
 name:name
 context_window:context
 max_tokens:token count
 reasoning:support mode
 
"""
    id: str
    name: str = ""
    context_window: int = 200000
    max_tokens: int = 8192
    reasoning: bool = False


class ProviderConfig(BaseModel):
    """

Single LLM provider configuration

    Attributes:
        base_url:API
        api_key:API Key(support ${ENV_VAR})
        api_type:type(openai / anthropic / google)
        models:optionalmodel
    
"""
    base_url: str = ""
    api_key: str = ""
    api_type: str = "openai"
    models: list[ModelDefinition] = Field(default_factory=list)

from app.atlasclaw.models.provider_presets import BUILTIN_PROVIDERS, PROVIDER_MODELS

class ProviderNotFoundError(Exception):
    """to Provider"""
    pass


class ModelCreationError(Exception):
    """modelcreate"""
    pass


# ============================================================
# ProviderRegistry
# ============================================================

class ProviderRegistry:
    """

Provider registry

    management registerproviderconfiguration, userconfiguration.

    Example usage:
        ```python
        registry = ProviderRegistry()
        registry.auto_discover()
        registry.load_from_config(user_providers)

        config = registry.get_provider("openai")
        ```
    
"""

    def __init__(self):
        self._providers: dict[str, ProviderConfig] = {}

    def register(self, name: str, config: ProviderConfig) -> None:
        """
registerprovider

        Args:
            name:providername
            config:providerconfiguration
        
"""
        # parseenvironment variable
        resolved_key = resolve_env(config.api_key)
        resolved_url = resolve_env(config.base_url)
        self._providers[name] = ProviderConfig(
            base_url=resolved_url,
            api_key=resolved_key,
            api_type=config.api_type,
            models=config.models,
        )
        logger.info(
            "Registered Provider: %s (api_type=%s, api_key=%s)",
            name, config.api_type, mask_api_key(resolved_key),
        )

    def get_provider(self, name: str) -> Optional[ProviderConfig]:
        """
getproviderconfiguration

        Args:
            name:providername

        Returns:
            ProviderConfig or None
        
"""
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        """registerprovidername"""
        return list(self._providers.keys())

    def is_available(self, name: str) -> bool:
        """

check provider available(register api_key or key)

        Args:
            name:providername

        Returns:
            available
        
"""
        config = self._providers.get(name)
        if config is None:
            return False
        # ollama etc. api_key
        preset = BUILTIN_PROVIDERS.get(name)
        if preset and not preset.env_key:
            return True
        return bool(config.api_key)

    def load_from_config(self, providers: dict[str, Any]) -> None:
        """

fromuserconfiguration Provider

        userconfiguration.

        Args:
            providers:{name:{base_url, api_key, api_type,...}} dictionary
        
"""
        for name, cfg in providers.items():
            if isinstance(cfg, dict):
                # default value
                preset = BUILTIN_PROVIDERS.get(name)
                if preset:
                    cfg.setdefault("base_url", preset.base_url)
                    cfg.setdefault("api_type", preset.api_type)
                    if "api_key" not in cfg and preset.env_key:
                        cfg["api_key"] = f"${{{preset.env_key}}}"
                self.register(name, ProviderConfig(**cfg))
            elif isinstance(cfg, ProviderConfig):
                self.register(name, cfg)

    def auto_discover(self) -> list[str]:
        """

environment variable, register provider

        Returns:
            registerprovidernamelist
        
"""
        discovered = []
        for name, preset in BUILTIN_PROVIDERS.items():
            if name in self._providers:
                continue
            # check environment variable at
            if preset.env_key and os.environ.get(preset.env_key):
                self.register(name, ProviderConfig(
                    base_url=preset.base_url,
                    api_key=f"${{{preset.env_key}}}",
                    api_type=preset.api_type,
                ))
                discovered.append(name)
            elif not preset.env_key:
                # key(such as ollama), register
                pass
        return discovered


# ============================================================
# ModelFactory
# ============================================================

def parse_model_ref(ref: str) -> tuple[str, str]:
    """

parsemodel characters

    Args:
        ref:model, for mat "provider/model" or "model"

    Returns:
        (provider_name, model_id)
    
"""
    if "/" in ref:
        provider, model_id = ref.split("/", 1)
        return provider, model_id
    # prefix default openai
    return "openai", ref


class ModelFactory:
    """


Model factory

 convert provider/model characters parse PydanticAI Model instance.

 Example usage:
 ```python
 factory = ModelFactory(registry)
 model = factory.create_model("openai/gpt-4o")
 agent = Agent(model, deps_type=SkillDeps)
 ```
 
"""

    def __init__(self, registry: ProviderRegistry):
        """
initialize ModelFactory

        Args:
            registry:Provider registry
        
"""
        self.registry = registry

    def create_model(self, model_ref: str) -> Any:
        """

create PydanticAI Model instance

        Args:
            model_ref:model(such as "openai/gpt-4o")

        Returns:
            PydanticAI Model instance

        Raises:
            Provider-Not-Found-Error:to Provider
            Model-Creation-Error:modelcreate
        
"""
        provider_name, model_id = parse_model_ref(model_ref)
        config = self.registry.get_provider(provider_name)

        if config is None:
            available = self.registry.list_providers()
            raise ProviderNotFoundError(
                f"Provider '{provider_name}' not registered. "
                f"Available Providers: {available}"
            )

        try:
            if config.api_type == "openai":
                return self._create_openai_model(model_id, config)
            elif config.api_type == "anthropic":
                return self._create_anthropic_model(model_id, config)
            elif config.api_type == "google":
                return self._create_google_model(model_id, config)
            else:
                raise ModelCreationError(
                    f"Unsupported api_type: '{config.api_type}'. "
                    f"Supported: openai, anthropic, google"
                )
        except (ProviderNotFoundError, ModelCreationError):
            raise
        except Exception as e:
            raise ModelCreationError(
                f"Failed to create model '{model_ref}': {e}"
            ) from e

    def _create_openai_model(self, model_id: str, config: ProviderConfig) -> Any:
        """Create an OpenAI chat model instance."""
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModelProfile
        from pydantic_ai.providers.openai import OpenAIProvider

        provider_kwargs: dict[str, Any] = {}
        if config.base_url:
            provider_kwargs["base_url"] = config.base_url
        if config.api_key:
            provider_kwargs["api_key"] = config.api_key

        provider_kwargs.setdefault("http_client", create_traced_http_client("openai"))
        provider = OpenAIProvider(**provider_kwargs)

        # Check if reasoning mode is enabled via model configuration
        model_def = next((m for m in config.models if m.id == model_id), None)
        has_reasoning = model_def.reasoning if model_def else False

        model_kwargs: dict[str, Any] = {"provider": provider}

        if has_reasoning:
            # Configure thinking field for OpenAI-compatible reasoning models
            # This works for DeepSeek, OpenRouter, vLLM, and other compatible APIs
            model_kwargs["profile"] = OpenAIModelProfile(
                openai_chat_thinking_field="reasoning_content",
            )

        return OpenAIChatModel(model_id, **model_kwargs)

    def _create_anthropic_model(self, model_id: str, config: ProviderConfig) -> Any:
        """
        Create Anthropic model.

        Args:
            model_id: model ID
            config: Provider configuration

        Returns:
            AnthropicModel instance
        """
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        provider_kwargs: dict[str, Any] = {}
        if config.api_key:
            provider_kwargs["api_key"] = config.api_key
        if config.base_url:
            provider_kwargs["base_url"] = config.base_url

        provider_kwargs.setdefault("http_client", create_traced_http_client("anthropic"))
        provider = AnthropicProvider(**provider_kwargs)

        # Check if reasoning mode is enabled via model configuration
        model_def = next((m for m in config.models if m.id == model_id), None)
        has_reasoning = model_def.reasoning if model_def else False

        model_kwargs: dict[str, Any] = {"provider": provider}
        if has_reasoning:
            # Configure thinking for Anthropic Claude models
            from pydantic_ai.models.anthropic import AnthropicModelSettings
            model_kwargs["model_settings"] = AnthropicModelSettings(
                anthropic_thinking={"type": "enabled", "budget_tokens": 10000}
            )

        return AnthropicModel(model_id, **model_kwargs)

    def _create_google_model(self, model_id: str, config: ProviderConfig) -> Any:
        """
        Create Google Gemini model.

        Args:
            model_id: model ID
            config: Provider configuration

        Returns:
            GoogleModel instance
        """
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        provider_kwargs: dict[str, Any] = {}
        if config.api_key:
            provider_kwargs["api_key"] = config.api_key

        provider = GoogleProvider(**provider_kwargs)

        # Check if reasoning mode is enabled via model configuration
        model_def = next((m for m in config.models if m.id == model_id), None)
        has_reasoning = model_def.reasoning if model_def else False

        model_kwargs: dict[str, Any] = {"provider": provider}
        if has_reasoning:
            # Configure thinking for Google Gemini models
            from pydantic_ai.models.google import GoogleModelSettings
            model_kwargs["model_settings"] = GoogleModelSettings(
                google_thinking_config={"include_thoughts": True}
            )

        return GoogleModel(model_id, **model_kwargs)


# ============================================================
# Global Registry Functions
# ============================================================

_default_registry: Optional[ProviderRegistry] = None
_default_factory: Optional[ModelFactory] = None


def get_provider_registry() -> ProviderRegistry:
    """get Provider-Registry"""
    global _default_registry
    if _default_registry is None:
        _default_registry = ProviderRegistry()
    return _default_registry


def get_model_factory() -> ModelFactory:
    """get Model-Factory"""
    global _default_factory
    if _default_factory is None:
        _default_factory = ModelFactory(get_provider_registry())
    return _default_factory


def init_providers(
    providers_config: Optional[dict[str, Any]] = None,
    auto_discover: bool = True,
) -> ProviderRegistry:
    """


initialize Provider

 Args:
 providers_config:userconfiguration providers dictionary(optional)
 auto_discover:environment variablein Provider

 Returns:
 initialize Provider-Registry
 
"""
    global _default_registry, _default_factory

    registry = ProviderRegistry()

    # 1. userconfiguration()
    if providers_config:
        registry.load_from_config(providers_config)

    # 2. environment variablein Provider
    if auto_discover:
        discovered = registry.auto_discover()
        if discovered:
            logger.info("Auto-discovered Providers: %s", discovered)

    _default_registry = registry
    _default_factory = ModelFactory(registry)

    logger.info("Provider system initialized, registered: %s", registry.list_providers())
    return registry
