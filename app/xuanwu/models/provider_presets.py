# -*- coding: utf-8 -*-
"""Built-in provider presets and model lists."""

from __future__ import annotations

from dataclasses import dataclass

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
