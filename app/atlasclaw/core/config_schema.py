"""Pydantic configuration schema definitions."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.atlasclaw.heartbeat.models import HeartbeatTargetType

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
    allow_script_execution: bool = Field(
        default=True,
        description="Whether markdown skill entrypoints may fall back to direct script/subprocess execution",
    )


class HookScriptHandlerConfig(BaseModel):
    """Config-driven local command script hook handler."""

    module: str = Field(description="Stable hook module name")
    events: list[str] = Field(default_factory=list, description="Subscribed hook event types")
    command: list[str] = Field(default_factory=list, description="Local executable command")
    timeout_seconds: int = Field(default=10, ge=1, le=300)
    enabled: bool = Field(default=False)
    cwd: Optional[str] = None
    priority: int = Field(default=100, ge=0)


class HooksRuntimeConfig(BaseModel):
    """Hook runtime extension configuration."""

    script_handlers: list[HookScriptHandlerConfig] = Field(default_factory=list)


class HeartbeatTargetConfig(BaseModel):
    """Heartbeat target descriptor configuration."""

    type: HeartbeatTargetType = Field(default=HeartbeatTargetType.NONE)
    user_id: str = ""
    channel: str = ""
    account_id: str = ""
    peer_id: str = ""
    session_key: str = ""
    thread_id: str = ""


class HeartbeatActiveHoursConfig(BaseModel):
    """Optional heartbeat active-hours constraint."""

    timezone: str = Field(default="Asia/Shanghai")
    start: str = Field(default="09:00")
    end: str = Field(default="22:00")


class HeartbeatRuntimeConfig(BaseModel):
    """Global heartbeat runtime config."""

    model_config = ConfigDict(populate_by_name=True)

    tick_seconds: int = Field(default=30, ge=1)
    max_concurrent_jobs: int = Field(default=16, ge=1)
    emit_runtime_events: bool = Field(default=True, alias="event_reporting")
    persist_local_event_log: bool = True


class HeartbeatDefaultsConfig(BaseModel):
    """Shared heartbeat defaults."""

    active_hours: HeartbeatActiveHoursConfig = Field(default_factory=HeartbeatActiveHoursConfig)


class AgentHeartbeatConfig(BaseModel):
    """Agent-turn heartbeat defaults."""

    enabled: bool = False
    every_seconds: int = Field(default=3600, ge=1)
    isolated_session: bool = True
    light_context: bool = True
    silent_ok: bool = True
    heartbeat_file: str = "HEARTBEAT.md"
    target: HeartbeatTargetConfig = Field(default_factory=HeartbeatTargetConfig)


class ChannelHeartbeatConfig(BaseModel):
    """Channel-connection heartbeat defaults."""

    enabled: bool = False
    check_interval_seconds: int = Field(default=30, ge=1)
    failure_threshold: int = Field(default=3, ge=1)
    degraded_threshold: int = Field(default=3, ge=1)
    reconnect_backoff_seconds: list[int] = Field(default_factory=lambda: [10, 30, 60, 300])


class HeartbeatConfig(BaseModel):
    """Unified heartbeat runtime configuration."""

    enabled: bool = False
    runtime: HeartbeatRuntimeConfig = Field(default_factory=HeartbeatRuntimeConfig)
    defaults: HeartbeatDefaultsConfig = Field(default_factory=HeartbeatDefaultsConfig)
    agent_turn: AgentHeartbeatConfig = Field(default_factory=AgentHeartbeatConfig)
    channel_connection: ChannelHeartbeatConfig = Field(default_factory=ChannelHeartbeatConfig)


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
    systems: list[WebhookSystemConfig] = Field(default_factory=list)


class TokenConfig(BaseModel):
    """Single token endpoint configuration."""

    id: str
    provider: str
    model: str
    base_url: str
    api_key: str
    api_type: str = "openai"
    priority: int = 0
    weight: int = 100
    context_window: Optional[int] = None


class ModelConfig(BaseModel):
    """Model configuration."""

    primary: str = Field(
        default="main", description="Primary token id referencing an entry in tokens[]"
    )
    fallbacks: list[str] = Field(default_factory=list, description="Fallback token ids")
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = None
    selection_strategy: str = Field(default="health", description="Token selection strategy")
    tokens: list[TokenConfig] = Field(default_factory=list, description="Token pool configuration")
    providers: dict[str, Any] = Field(
        default_factory=dict,
        description="Legacy LLM provider configuration, {name: {base_url, api_key, api_type, models}}",
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
    max_concurrent: int = Field(default=10, ge=1, description="Maximum concurrency")
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
    path: str = Field(default="./.atlasclaw", description="Workspace path, defaults to ./.atlasclaw directory")


class SqliteDatabaseConfig(BaseModel):
    """SQLite database configuration."""
    path: str = Field(default="./data/atlasclaw.db", description="Path to SQLite database file")


class MySqlDatabaseConfig(BaseModel):
    """MySQL database configuration."""
    host: str = Field(default="localhost", description="MySQL host")
    port: int = Field(default=3306, ge=1, le=65535, description="MySQL port")
    database: str = Field(default="atlasclaw", description="Database name")
    user: str = Field(default="root", description="Database user")
    password: str = Field(default="", description="Database password")
    charset: str = Field(default="utf8mb4", description="Character set")


class DatabaseConfig(BaseModel):
    """Database configuration for SQLite or MySQL."""
    type: str = Field(default="sqlite", description="Database type: sqlite or mysql")
    sqlite: Optional[SqliteDatabaseConfig] = Field(default_factory=SqliteDatabaseConfig, description="SQLite configuration")
    mysql: Optional[MySqlDatabaseConfig] = Field(default=None, description="MySQL configuration")
    pool_size: int = Field(default=5, ge=1, description="Connection pool size (MySQL only)")
    max_overflow: int = Field(default=10, ge=0, description="Max overflow connections (MySQL only)")
    echo: bool = Field(default=False, description="Echo SQL statements for debugging")


class UserConfig(BaseModel):
    """User-specific configuration stored in users/<id>/user_setting.json
    
    Note: providers are system-level configuration, not user-level.
    """
    channels: dict[str, Any] = Field(
        default_factory=dict,
        description="User-level channel configurations (e.g., Feishu bot, DingTalk bot)"
    )
    preferences: dict[str, Any] = Field(
        default_factory=dict,
        description="General user preferences (language, timezone, etc.)"
    )


class AtlasClawConfig(BaseModel):
    """AtlasClaw configuration"""
    log_level: LogLevel = LogLevel.INFO
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig, description="Workspace configuration")
    database: Optional[DatabaseConfig] = Field(default=None, description="Database configuration")
    agents_dir: str = Field(default="~/.atlasclaw/agents", description="Agent directory (backward compatibility)")
    providers_root: str = Field(
        default="../providers",
        description="Root directory for provider templates and skills, resolved relative to atlasclaw.json",
    )
    skills_root: str = Field(
        default="../skills",
        description="Root directory for standalone skills (not tied to providers), resolved relative to atlasclaw.json",
    )
    channels_root: str = Field(
        default="../channels",
        description="Root directory for system-level channel configurations, resolved relative to atlasclaw.json",
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
    hooks_runtime: HooksRuntimeConfig = Field(default_factory=HooksRuntimeConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)

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
