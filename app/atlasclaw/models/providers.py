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
from dataclasses import dataclass, field
from typing import Optional, Any

from pydantic import BaseModel, Field

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


@dataclass
class ProviderPreset:
    """

Built-in provider preset

    Attributes:
        base_url:default API
        api_type:type
        env_key:corresponds toenvironment variablename
    
"""
    base_url: str = ""
    api_type: str = "openai"
    env_key: str = ""


# ============================================================
# Provider Presets
# ============================================================

BUILTIN_PROVIDERS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        base_url="https://api.openai.com/v1",
        api_type="openai",
        env_key="OPENAI_API_KEY",
    ),
    "anthropic": ProviderPreset(
        base_url="https://api.anthropic.com",
        api_type="anthropic",
        env_key="ANTHROPIC_API_KEY",
    ),
    "google": ProviderPreset(
        api_type="google",
        env_key="GEMINI_API_KEY",
    ),
    "ollama": ProviderPreset(
        base_url="http://127.0.0.1:11434/v1",
        api_type="openai",
        env_key="",
    ),
    "vllm": ProviderPreset(
        base_url="http://127.0.0.1:8080/v1",
        api_type="openai",
        env_key="VLLM_API_KEY",
    ),
    "moonshot": ProviderPreset(
        base_url="https://api.moonshot.ai/v1",
        api_type="openai",
        env_key="MOONSHOT_API_KEY",
    ),
    "groq": ProviderPreset(
        base_url="https://api.groq.com/openai/v1",
        api_type="openai",
        env_key="GROQ_API_KEY",
    ),
    "deepseek": ProviderPreset(
        base_url="https://api.deepseek.com/v1",
        api_type="openai",
        env_key="DEEPSEEK_API_KEY",
    ),
    "doubao": ProviderPreset(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_type="openai",
        env_key="DOUBAO_API_KEY",
    ),
    "minimax": ProviderPreset(
        base_url="https://api.minimax.io/v1",
        api_type="openai",
        env_key="MINIMAX_API_KEY",
    ),
    "qwen": ProviderPreset(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_type="openai",
        env_key="QWEN_API_KEY",
    ),
    "zhipu": ProviderPreset(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_type="openai",
        env_key="ZHIPU_API_KEY",
    ),
    "baichuan": ProviderPreset(
        base_url="https://api.baichuan-ai.com/v1",
        api_type="openai",
        env_key="BAICHUAN_API_KEY",
    ),
    "yi": ProviderPreset(
        base_url="https://api.lingyiwanwu.com/v1",
        api_type="openai",
        env_key="YI_API_KEY",
    ),
    "stepfun": ProviderPreset(
        base_url="https://api.stepfun.com/v1",
        api_type="openai",
        env_key="STEPFUN_API_KEY",
    ),
    "siliconflow": ProviderPreset(
        base_url="https://api.siliconflow.cn/v1",
        api_type="openai",
        env_key="SILICONFLOW_API_KEY",
    ),
    "mistral": ProviderPreset(
        base_url="https://api.mistral.ai/v1",
        api_type="openai",
        env_key="MISTRAL_API_KEY",
    ),
    "cohere": ProviderPreset(
        base_url="https://api.cohere.ai/compatibility/v1",
        api_type="openai",
        env_key="COHERE_API_KEY",
    ),
    "spark": ProviderPreset(
        base_url="https://spark-api-open.xf-yun.com/v1",
        api_type="openai",
        env_key="SPARK_API_KEY",
    ),
    "hunyuan": ProviderPreset(
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        api_type="openai",
        env_key="HUNYUAN_API_KEY",
    ),
}


# ============================================================
# Built-in model presets per provider
# ============================================================

PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o3",
        "o3-mini",
        "o3-pro",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "o1",
        "o1-mini",
        "o1-preview",
    ],
    "anthropic": [
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
    ],
    "google": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-coder",
        "deepseek-chat-v3-0324",
        "deepseek-reasoner-v3-0324",
    ],
    "qwen": [
        "qwen3-max",
        "qwen3-plus",
        "qwen3-turbo",
        "qwen3-flash",
        "qwen3-coder-plus",
        "qwen-max",
        "qwen-plus",
        "qwen-turbo",
        "qwen-long",
        "qwen2.5-72b-instruct",
        "qwen2.5-32b-instruct",
        "qwen2.5-14b-instruct",
        "qwen2.5-7b-instruct",
        "qwen-vl-max",
        "qwen-vl-plus",
        "qwq-32b",
    ],
    "zhipu": [
        "glm-4-plus",
        "glm-4",
        "glm-4-flash",
        "glm-4-long",
        "glm-4-air",
        "glm-4v",
    ],
    "minimax": [
        "MiniMax-Text-01",
        "abab6.5s-chat",
        "abab6.5-chat",
        "abab5.5-chat",
    ],
    "baichuan": [
        "Baichuan4",
        "Baichuan3-Turbo",
        "Baichuan3-Turbo-128k",
        "Baichuan2-Turbo",
    ],
    "yi": [
        "yi-lightning",
        "yi-large",
        "yi-large-turbo",
        "yi-medium",
        "yi-spark",
    ],
    "stepfun": [
        "step-2-16k",
        "step-1-256k",
        "step-1-32k",
        "step-1-8k",
        "step-1-flash",
    ],
    "moonshot": [
        "kimi-k2",
        "moonshot-v1-128k",
        "moonshot-v1-32k",
        "moonshot-v1-8k",
        "kimi-latest",
    ],
    "spark": [
        "generalv3.5",
        "4.0Ultra",
        "generalv3",
        "general",
    ],
    "hunyuan": [
        "hunyuan-pro",
        "hunyuan-standard",
        "hunyuan-lite",
        "hunyuan-turbo",
        "hunyuan-vision",
    ],
    "doubao": [
        "doubao-pro-256k",
        "doubao-pro-128k",
        "doubao-pro-32k",
        "doubao-lite-128k",
        "doubao-lite-32k",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "deepseek-r1-distill-llama-70b",
    ],
    "mistral": [
        "mistral-large-latest",
        "mistral-medium-latest",
        "mistral-small-latest",
        "open-mistral-nemo",
        "codestral-latest",
    ],
    "cohere": [
        "command-a-03-2025",
        "command-r-plus-08-2024",
        "command-r-08-2024",
        "command-r7b-12-2024",
        "command-r-plus",
        "command-r",
    ],
    "siliconflow": [
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct",
        "Qwen/QwQ-32B",
        "THUDM/glm-4-9b-chat",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "meta-llama/Meta-Llama-3.1-8B-Instruct",
    ],
    "ollama": [
        "qwen3",
        "llama3.3",
        "llama3.1",
        "llama3",
        "qwen2.5",
        "deepseek-r1",
        "deepseek-v3",
        "mistral",
        "codellama",
        "gemma3",
        "gemma2",
        "phi4",
        "nomic-embed-text",
    ],
    "vllm": [
        "meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/Llama-3.1-70B-Instruct",
        "meta-llama/Llama-3.1-8B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct",
        "mistralai/Mistral-7B-Instruct-v0.3",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1",
    ],
}


# ============================================================
# Exceptions
# ============================================================

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
