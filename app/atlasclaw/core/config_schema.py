"""Pydantic configuration schema definitions."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

# Auth config is imported lazily to avoid circular imports at module load time.
# AuthConfig is referenced only in AtlasClawConfig.auth field annotation.


class LogLevel(str, Enum):
    """Supported log levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QueueModeConfig(str, Enum):
    """Supported queue modes."""
    COLLECT = "collect"
    STEER = "steer"
    FOLLOWUP = "followup"
    STEER_BACKLOG = "steer-backlog"
    INTERRUPT = "interrupt"


class DropStrategy(str, Enum):
    """Queue overflow strategies."""
    OLD = "old"
    NEW = "new"
    SUMMARIZE = "summarize"


class ResetMode(str, Enum):
    """Supported session reset policies."""
    DAILY = "daily"
    IDLE = "idle"
    MANUAL = "manual"


class PromptMode(str, Enum):
    """Supported system prompt modes."""
    FULL = "full"
    MINIMAL = "minimal"
    NONE = "none"


class SandboxMode(str, Enum):
    """Supported sandbox modes."""
    OFF = "off"
    AGENT = "agent"
    SESSION = "session"


class HumanDelayMode(str, Enum):
    """Supported human-like delay modes."""
    OFF = "off"
    NATURAL = "natural"
    CUSTOM = "custom"


# ============================================================
# Configuration models
# ============================================================

class QueueConfig(BaseModel):
    """Queue configuration."""
    mode: QueueModeConfig = QueueModeConfig.COLLECT
    debounce_ms: int = Field(default=1000, ge=0, description="Debounce wait time in milliseconds")
    cap: int = Field(default=20, ge=1, description="Maximum queued messages per session")
    drop: DropStrategy = DropStrategy.OLD


class ResetConfig(BaseModel):
    """Session reset configuration."""
    mode: ResetMode = ResetMode.DAILY
    daily_hour: int = Field(default=4, ge=0, le=23, description="Daily reset hour")
    idle_minutes: int = Field(default=60, ge=1, description="Idle reset threshold in minutes")


class CompactionConfig(BaseModel):
    """Compaction configuration"""
    reserve_tokens_floor: int = Field(default=20000, description="Tokens reserved for new responses")
    soft_threshold_tokens: int = Field(default=4000, description="Soft threshold for triggering memory refresh")
    context_window: int = Field(default=128000, description="Model context window size")
    memory_flush_enabled: bool = True


class BlockChunkerConfig(BaseModel):
    """Streaming block chunking configuration."""
    min_chars: int = Field(default=800, ge=1, description="Minimum chunk size in characters")
    max_chars: int = Field(default=1200, ge=1, description="Maximum chunk size in characters")
    break_preference: str = Field(default="paragraph", description="Preferred chunk break strategy")
    idle_ms: int = Field(default=300, ge=0, description="Idle flush interval in milliseconds")


class HumanDelayConfig(BaseModel):
    """Human-like delay configuration"""
    mode: HumanDelayMode = HumanDelayMode.OFF
    min_ms: int = Field(default=800, ge=0)
    max_ms: int = Field(default=2500, ge=0)


class SandboxConfig(BaseModel):
    """Sandbox configuration"""
    enabled: bool = False
    mode: SandboxMode = SandboxMode.OFF
    workspace_root: str = ""
    elevated_exec: bool = False


class SecurityPolicyConfig(BaseModel):
    """Security policy configuration."""
    allowed_tools: list[str] = Field(default_factory=list, description="Allowed tools list (empty means all tools are allowed)")
    denied_tools: list[str] = Field(default_factory=list, description="Denied tools list (takes priority, supports * wildcards)")
    workspace_access: str = Field(default="rw", description="Workspace access level: rw | ro | none")


class SkillsConfig(BaseModel):
    """MD Skills configuration"""
    md_skills_max_count: int = Field(default=20, ge=1, description="Maximum number of MD skills shown in the index section")
    md_skills_desc_max_chars: int = Field(default=200, ge=1, description="Maximum characters for a single skill description")
    md_skills_index_max_chars: int = Field(default=3000, ge=1, description="Maximum total characters for the index section")
    md_skills_max_file_bytes: int = Field(default=262144, ge=1, description="Maximum size of a single SKILL.md file in bytes (default 256KB)")


class WebhookSkillSourceConfig(BaseModel):
    """Additional markdown-skill roots addressable by provider-qualified name."""
    provider: str = Field(description="Provider namespace used in provider:skill identifiers")
    root: str = Field(description="Skills root directory; relative paths are resolved against providers_root first")


class WebhookSystemConfig(BaseModel):
    """Per-system webhook access configuration."""
    system_id: str = Field(description="Stable identifier for the external system")
    enabled: bool = True
    sk_env: str = Field(description="Environment variable that stores the shared secret")
    default_agent_id: str = "main"
    allowed_skills: list[str] = Field(default_factory=list)


class WebhookConfig(BaseModel):
    """Inbound webhook dispatch configuration."""
    enabled: bool = False
    header_name: str = "X-AtlasClaw-SK"
    skill_sources: list[WebhookSkillSourceConfig] = Field(default_factory=list)
    systems: list[WebhookSystemConfig] = Field(default_factory=list)


class ModelConfig(BaseModel):
    """Model configuration."""
    primary: str = Field(default="doubao-pro-32k", description="Primary model in provider/model format")
    fallbacks: list[str] = Field(default_factory=list, description="Fallback model list")
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = None
    providers: dict[str, Any] = Field(
        default_factory=dict,
        description="LLM provider configuration, {name: {base_url, api_key, api_type, models}}",
    )


class RetryConfig(BaseModel):
    """Retry configuration"""
    attempts: int = Field(default=3, ge=1)
    min_delay_ms: int = Field(default=1000, ge=0)
    max_delay_ms: int = Field(default=30000, ge=0)
    jitter: float = Field(default=0.1, ge=0, le=1)


class AgentDefaultsConfig(BaseModel):
    """Default agent configuration."""
    timeout_seconds: int = Field(default=600, ge=1, description="Execution timeout in seconds")
    max_concurrent: int = Field(default=4, ge=1, description="Maximum concurrency")
    max_tool_calls: int = Field(default=50, ge=1, description="Maximum tool calls per run")
    prompt_mode: PromptMode = PromptMode.FULL
    bootstrap_max_chars: int = Field(default=20000, description="Maximum Bootstrap file size in characters")
    block_streaming_default: bool = False
    block_streaming_break: str = "text_end"
    human_delay: HumanDelayConfig = Field(default_factory=HumanDelayConfig)


class MessagesConfig(BaseModel):
    """Message handling configuration."""
    queue: QueueConfig = Field(default_factory=QueueConfig)
    response_prefix: str = ""
    reply_to_mode: str = "auto"
    inbound_debounce_ms: int = Field(default=1000, ge=0)
    dedup_ttl_seconds: int = Field(default=60, ge=1)


class MemoryConfig(BaseModel):
    """Memory configuration."""
    enabled: bool = True
    vector_weight: float = Field(default=0.7, ge=0, le=1, description="Vector search weight")
    fulltext_weight: float = Field(default=0.3, ge=0, le=1, description="Full-text search weight")
    time_decay_half_life_days: float = Field(default=30.0, ge=1, description="Time decay half-life in days")
    max_results: int = Field(default=6, ge=1)


class WorkspaceConfig(BaseModel):
    """Workspace configuration"""
    path: str = Field(default=".", description="Workspace path, defaults to the current directory")
    per_user_isolation: bool = Field(default=True, description="Whether to isolate data per user")


class AtlasClawConfig(BaseModel):
    """AtlasClaw configuration"""
    log_level: LogLevel = LogLevel.INFO
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig, description="Workspace configuration")
    agents_dir: str = Field(default="~/.atlasclaw/agents", description="Agent directory (backward compatibility)")
    providers_root: str = Field(
        default="../atlasclaw-providers/providers",
        description="Root directory for provider templates and skills, resolved relative to atlasclaw.json",
    )
    
    # Nested configuration sections
    agent_defaults: AgentDefaultsConfig = Field(default_factory=AgentDefaultsConfig)
    messages: MessagesConfig = Field(default_factory=MessagesConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    block_chunker: BlockChunkerConfig = Field(default_factory=BlockChunkerConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    security: SecurityPolicyConfig = Field(default_factory=SecurityPolicyConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    reset: ResetConfig = Field(default_factory=ResetConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)

    # Auth configuration — loaded from `auth` section of atlasclaw.json.
    # None means no auth config present; runtime falls back to anonymous mode.
    auth: Optional[Any] = Field(
        default=None,
        description="Authentication configuration mapped from the auth section of atlasclaw.json; falls back to anonymous mode when missing",
    )
    
    # Service provider instance configuration.
    # Format: {provider_type: {instance_name: {param: value}}}
    service_providers: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Enterprise service provider instance configuration, {type: {instance: {params}}}",
    )
