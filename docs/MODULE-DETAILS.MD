# AtlasClaw Module Details

> **Canonical per-module API reference for the AtlasClaw enterprise agent framework.**
> All future development and feature changes MUST consult this document.
>
> Related documents:
> - [Architecture](./ARCHITECTURE.MD) -system design, bootstrap, request lifecycle
> - [Development Specification](./DEVELOPMENT-SPEC.MD) -code standards, patterns, review checklist

---

## Table of Contents

1. [Core Module](#1-core-module)
2. [Auth Module](#2-auth-module)
3. [Agent Module](#3-agent-module)
4. [API Module](#4-api-module)
5. [Channels Module](#5-channels-module)
6. [Tools Module](#6-tools-module)
7. [Session Module](#7-session-module)
8. [Memory Module](#8-memory-module)
9. [Skills Module](#9-skills-module)
10. [Hooks Module](#10-hooks-module)
11. [Models Module](#11-models-module)
12. [Messages Module](#12-messages-module)
13. [Workflow Module](#13-workflow-module)
14. [Media Module](#14-media-module)
15. [Frontend](#15-frontend)

---

## 1. Core Module

**Path**: `app/atlasclaw/core/`

Provides configuration management, request-scoped dependency injection, workspace initialization, service provider registry, provider scanning, multi-tenant isolation, token pool management, and encryption services for sensitive data.

### 1.1 ConfigManager (`core/config.py`)

Singleton configuration manager with a 6-level priority chain.

**Priority Chain** (highest ->lowest):
1. Runtime overrides via `config_manager.set()`
2. Environment variables (`ATLASCLAW_<SECTION>__<KEY>`)
3. User config (`~/.atlasclaw/config.json`)
4. Workspace config (`.atlasclaw/atlasclaw.json`)
5. Project config file (`atlasclaw.json` / `atlasclaw.yaml`)
6. Schema defaults (Pydantic model defaults)

**Class: `ConfigManager`**

| Method | Signature | Description |
|---|---|---|
| `load` | `() -> AtlasClawConfig` | Multi-source config loading; lazy on first access |
| `reload` | `() -> AtlasClawConfig` | Force reload from all sources |
| `set` | `(key: str, value: Any) -> None` | Runtime override with dotted key path (e.g. `"agent_defaults.timeout_seconds"`) |
| `get` | `(key: str, default: Any) -> Any` | Get nested config value by dotted key |
| `load_user_config` | `(user_id: str) -> dict` | Load per-user configuration from `<workspace>/users/<user_id>/atlasclaw.json` |

**Properties**:
- `config: AtlasClawConfig` -Lazily loaded configuration object
- `resolved_config_path: Optional[Path]` -Absolute path of the loaded config file

**Module-Level Functions**:
- `get_config_manager() -> ConfigManager` -Get singleton instance
- `get_config() -> AtlasClawConfig` -Shorthand for `get_config_manager().config`
- `get_config_path() -> Optional[Path]` -Get resolved config file path

### 1.2 SkillDeps (`core/deps.py`)

Request-scoped dependency container passed through `RunContext[SkillDeps]` to all tools and skills.

**Dataclass: `SkillDeps`** (`@dataclass(init=False)`)

| Field | Type | Default | Description |
|---|---|---|---|
| `user_info` | `UserInfo` | `ANONYMOUS_USER` | Authenticated user identity |
| `extra` | `dict[str, Any]` | `{}` | Additional request-scoped context including provider clients and channel-bound provider instance data |
| `peer_id` | `str` | `""` | Peer identifier (user or group ID) |
| `session_key` | `str` | `""` | Stable session key for current conversation |
| `channel` | `str` | `""` | Source channel name |
| `abort_signal` | `asyncio.Event` | new Event | Abort signal shared across current run |
| `session_manager` | `Optional[Any]` | `None` | Per-user scoped SessionManager |
| `memory_manager` | `Optional[Any]` | `None` | Per-user scoped MemoryManager |

**Methods**:

| Method | Signature | Description |
|---|---|---|
| `is_aborted` | `() -> bool` | Check if the current run has been aborted |
| `abort` | `() -> None` | Signal that the current run should stop |
| `reset_abort` | `() -> None` | Clear the abort signal |

**Property**:
- `user_token: str` -Alias for `user_info.raw_token` (backward compatibility)

**Constructor** accepts `user_token: Optional[str]` keyword argument for backward compatibility. If both `user_info` and `user_token` are provided, the token is merged into a new `UserInfo` instance.

### 1.3 Configuration Schema (`core/config_schema.py`)

Pydantic models defining all configuration sections.

**Enums**:

| Enum | Values | Usage |
|---|---|---|
| `LogLevel` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Logging verbosity |
| `QueueModeConfig` | `COLLECT`, `STEER`, `FOLLOWUP`, `STEER_BACKLOG`, `INTERRUPT` | Message queue behavior |
| `DropStrategy` | `OLD`, `NEW`, `SUMMARIZE` | Queue overflow handling |
| `ResetMode` | `DAILY`, `IDLE`, `MANUAL` | Session reset policy |
| `PromptMode` | `FULL`, `MINIMAL`, `NONE` | System prompt construction mode |
| `SandboxMode` | `OFF`, `AGENT`, `SESSION` | Sandbox isolation level |
| `HumanDelayMode` | `OFF`, `NATURAL`, `CUSTOM` | Response delay simulation |

**Config Models**:

| Model | Key Fields (with defaults) |
|---|---|
| `AtlasClawConfig` | `log_level=INFO`, `base_path=""`, `workspace`, `agents_dir`, `providers_root`, `agent_defaults`, `messages`, `compaction`, `block_chunker`, `model`, `retry`, `memory`, `sandbox`, `security`, `skills`, `reset`, `webhook`, `tool_gate`, `auth`, `service_providers` |
| `QueueConfig` | `mode=COLLECT`, `debounce_ms=1000`, `cap=20`, `drop=OLD` |
| `ResetConfig` | `mode=DAILY`, `daily_hour=4`, `idle_minutes=60` |
| `CompactionConfig` | `reserve_tokens_floor=20000`, `soft_threshold_tokens=4000`, `context_window=128000`, `memory_flush_enabled=True` |
| `BlockChunkerConfig` | `min_chars=800`, `max_chars=1200`, `break_preference="paragraph"`, `idle_ms=300` |
| `HumanDelayConfig` | `mode=OFF`, `min_ms=800`, `max_ms=2500` |
| `SandboxConfig` | `enabled=False`, `mode=OFF`, `workspace_root=""`, `elevated_exec=False` |
| `SecurityPolicyConfig` | `allowed_tools=[]`, `denied_tools=[]`, `workspace_access="rw"` |
| `SkillsConfig` | `enabled_skills=[]`, `md_skills_max_count=20`, `md_skills_desc_max_chars=200`, `md_skills_index_max_chars=3000`, `md_skills_max_file_bytes=262144`, `tools_exclusive=[]`, `allow_script_execution=True` |
| `WebhookConfig` | `enabled=False`, `header_name="X-AtlasClaw-SK"`, `systems=[]` |
| `WebhookSystemConfig` | `system_id`, `enabled=True`, `sk_env`, `default_agent_id="main"`, `allowed_skills=[]` |
| `ModelConfig` | `primary="doubao-pro-32k"`, `fallbacks=[]`, `temperature=0.7`, `max_tokens=None`, `providers={}` |
| `RetryConfig` | `attempts=3`, `min_delay_ms=1000`, `max_delay_ms=30000`, `jitter=0.1` |
| `AgentDefaultsConfig` | `timeout_seconds=600`, `max_concurrent=4`, `max_tool_calls=50`, `prompt_mode=FULL`, `bootstrap_max_chars=20000`, `block_streaming_default=False` |
| `MessagesConfig` | `queue`, `response_prefix=""`, `reply_to_mode="auto"`, `inbound_debounce_ms=1000`, `dedup_ttl_seconds=60` |
| `MemoryConfig` | `enabled=True`, `vector_weight=0.7`, `fulltext_weight=0.3`, `time_decay_half_life_days=30.0`, `max_results=6` |
| `WorkspaceConfig` | `path="./.atlasclaw"` |
| `TokenConfig` | `id`, `provider`, `model`, `base_url`, `api_key`, `api_type="openai"`, `priority=0`, `weight=100` |

### 1.4 Token Pool (`core/token_pool.py`)

Multi-token management for LLM API endpoints with health-aware selection.

**Dataclass: `TokenEntry`** -Single token endpoint configuration.

| Field | Type | Default | Description |
|---|---|---|---|
| `token_id` | `str` | -| Unique token identifier |
| `provider` | `str` | -| Provider name (e.g., `deepseek`, `openai`) |
| `model` | `str` | -| Model name (e.g., `deepseek-chat`) |
| `base_url` | `str` | -| API base URL |
| `api_key` | `str` | -| API key (interpolated from env) |
| `api_type` | `str` | `"openai"` | API type (`openai` or `anthropic`) |
| `priority` | `int` | `0` | Selection priority (higher = preferred) |
| `weight` | `int` | `100` | Selection weight for weighted strategies |

**Dataclass: `TokenHealth`** -Health snapshot for rate-limit tracking.

| Field | Type | Default | Description |
|---|---|---|---|
| `remaining_tokens` | `int` | `100000` | Remaining tokens in rate limit window |
| `remaining_requests` | `int` | `100` | Remaining requests in rate limit window |
| `reset_tokens_seconds` | `int` | `0` | Seconds until token limit resets |
| `reset_requests_seconds` | `int` | `0` | Seconds until request limit resets |
| `updated_at` | `datetime` | now (UTC) | Last update timestamp |

**Properties**:
- `health_score: float` -Computed score (0-100) based on remaining tokens/requests
- `is_healthy: bool` -True if `remaining_requests > 0` and `remaining_tokens > 100`

**Class: `TokenPool`** -Thread-safe token pool with health-aware selection.

| Method | Signature | Description |
|---|---|---|
| `register_token` | `(token: TokenEntry) -> None` | Register a new token |
| `restore_health` | `(token_id, health: TokenHealth) -> None` | Restore health from persistence |
| `update_token_health` | `(token_id, headers: dict) -> None` | Update from response headers |
| `select_token` | `(provider?, model?, strategy) -> TokenEntry?` | Select token by strategy |
| `get_token_health` | `(token_id) -> TokenHealth?` | Get health snapshot |
| `export_health_status` | `() -> dict[str, TokenHealth]` | Export all health states |

**Selection Strategies**:
- `health` -Select by health score (highest first), then priority/weight
- `priority` -Select by priority (highest first), then weight
- `random` -Weighted random selection
- `round_robin` -Rotate through available tokens

### 1.5 Token Health Store (`core/token_health_store.py`)

Persistence for token health snapshots.

**Class: `TokenHealthStore`**

Constructor: `workspace_path: str`

| Method | Signature | Description |
|---|---|---|
| `save` | `(health_status: dict[str, TokenHealth]) -> None` | Persist to `token_health.json` |
| `load` | `() -> dict[str, TokenHealth]` | Load from `token_health.json` |

**Storage Location**: `<workspace>/token_health.json`

### 1.6 Token Health Interceptor (`core/token_interceptor.py`)

Extracts rate-limit headers from LLM responses and updates token health.

**Class: `TokenHealthInterceptor`**

Constructor: `token_pool: TokenPool`, `health_store: TokenHealthStore`

| Method | Signature | Description |
|---|---|---|
| `on_response` | `(token_id: str, headers: dict) -> None` | Update health from response headers |

**Headers Extracted**:
- `x-ratelimit-remaining-tokens`
- `x-ratelimit-remaining-requests`
- `x-ratelimit-reset-tokens`
- `x-ratelimit-reset-requests`

### 1.7 Encryption Service (`core/encryption.py`)

AES-256-GCM encryption for sensitive data at rest.

**Class: `EncryptionService`**

Constructor: `key: bytes` (32-byte encryption key)

| Method | Signature | Description |
|---|---|---|
| `encrypt` | `(plaintext: str) -> str` | Encrypt string, returns `v1:key_id:base64(...)` format |
| `decrypt` | `(ciphertext: str) -> str` | Decrypt encrypted string |
| `encrypt_json` | `(data: dict) -> str` | Encrypt JSON-serializable dict |
| `decrypt_json` | `(ciphertext: str) -> dict` | Decrypt to dict |

**Key Management**:
- Primary key from `ATLASCLAW_ENCRYPTION_KEY` environment variable (base64-encoded 32 bytes)
- Built-in default key for development (must be overridden in production)
- Support for envelope encryption with rotating data keys (optional)

**Usage**:
```python
from app.atlasclaw.core.encryption import encrypt_json, decrypt_json

# Encrypt before storage
encrypted = encrypt_json({"api_key": "secret123"})
# Returns: "v1:default:AbCdEfGh..."

# Decrypt after retrieval
data = decrypt_json(encrypted)
# Returns: {"api_key": "secret123"}
```

**Config File Encryption**:
Values in `atlasclaw.json` with `enc:` prefix are automatically decrypted on load:
```json
{
  "model": {
    "tokens": {
      "my-token": {
        "api_key": "enc:v1:default:AbCdEfGh..."
      }
    }
  }
}
```

### 1.4 Workspace Initialization (`core/workspace.py`)

**Class: `WorkspaceInitializer`**

Creates the global workspace directory structure on first run:

```text
<workspace>/
|- agents/
|  \- main/
|     |- SOUL.md
|     |- IDENTITY.md
|     |- USER.md
|     \- MEMORY.md
\- users/
```

Default `agents/main/` files are copied from the repo template directory at
`app/atlasclaw/templates/agents/main/`. Existing files are preserved; only
missing files are restored.

| Method | Signature | Description |
|---|---|---|
| `initialize` | `() -> bool` | Create workspace structure; returns True if created |
| `is_initialized` | `() -> bool` | Check if workspace exists |

**Class: `UserWorkspaceInitializer`**

Creates per-user directory structure:

```text
users/<user_id>/
|- user_setting.json      # User-specific config (channels, preferences)
|- sessions/
\- memory/
```

| Method | Signature | Description |
|---|---|---|
| `initialize` | `() -> bool` | Create user directory structure |
| `is_initialized` | `() -> bool` | Check if user workspace exists |
| `get_sessions_dir` | `() -> Path` | Get user sessions directory |
| `get_memory_dir` | `() -> Path` | Get user memory directory |

**Class: `SessionManagerRouter` (`session/router.py`)**

Routes runtime session persistence to the correct `workspace/users/<user_id>/sessions/` bucket.

| Method | Signature | Description |
|---|---|---|
| `from_manager` | `(manager: SessionManager) -> SessionManagerRouter` | Clone reset/storage settings from an existing manager |
| `for_user` | `(user_id: str) -> SessionManager` | Resolve the per-user session manager |
| `for_session_key` | `(session_key: str) -> SessionManager` | Resolve storage from `SessionKey.user_id` |

### 1.5 Service Provider Registry (`core/provider_registry.py`)

Manages provider templates, instance configurations, and skill registration with instance awareness.

**Dataclass: `ProviderTemplate`**

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Provider type name |
| `path` | `Path` | Provider directory path |
| `md_path` | `Path` | PROVIDER.md path |
| `skills_dir` | `Path` | Skills subdirectory |

**Dataclass: `ProviderContext`** (LLM context for skill selection)

| Field | Type | Description |
|---|---|---|
| `provider_type` | `str` | Provider type identifier |
| `display_name` | `str` | Human-readable name |
| `version` | `str` | Provider version |
| `keywords` | `list[str]` | Search keywords |
| `capabilities` | `list[str]` | Provider capabilities |
| `use_when` | `list[str]` | When to use this provider |
| `avoid_when` | `list[str]` | When not to use this provider |
| `description` | `str` | Full description |

**Class: `ServiceProviderRegistry`**

| Method | Signature | Description |
|---|---|---|
| `load_from_directory` | `(providers_dir: Path) -> int` | Load provider templates from directory |
| `load_instances_from_config` | `(config: dict) -> None` | Load instance configurations (merged JSON + DB) |
| `list_providers` | `() -> list[str]` | List all provider types |
| `list_instances` | `(provider_type: str) -> list[str]` | List instances for a provider |
| `get_instance_config` | `(provider_type, instance_name) -> Optional[dict]` | Get raw instance config |
| `get_instance_config_redacted` | `(...) -> Optional[dict]` | Get config with secrets masked |
| `get_provider_context` | `(provider_type: str) -> Optional[ProviderContext]` | Get LLM context for provider |
| `register_skills_to` | `(skill_registry) -> int` | Register provider skills with instance wrappers |

**Instance Config Merge Behavior**:
1. JSON config (`atlasclaw.json` `service_providers`) loaded first
2. DB configs (`service_provider_configs` table) loaded second
3. DB entries override JSON entries with same `(provider_type, instance_name)`

Instance wrapper logic: when a skill is invoked, if the provider has a single instance it is auto-selected; if multiple instances exist, the LLM is prompted to choose.

**Environment Variable Resolution**: Config values containing `${VAR_NAME}` or `${VAR_NAME:default}` are resolved at load time.

**Database Persistence** (`app/atlasclaw/db/orm/service_provider_config.py`):

| Service Method | Description |
|---|---|
| `create(session, data)` | Create new provider instance config |
| `get_by_id(session, id)` | Get by primary key |
| `get_by_provider_instance(session, type, name)` | Get by unique (type, name) |
| `list_all(session, ...)` | Paginated list with filters |
| `list_active_as_nested(session)` | Return active configs as nested dict for merge |
| `update(session, id, data)` | Update config fields |
| `delete(session, id)` | Soft delete (or hard delete) |

**API Endpoints** (`/api/provider-configs`):
- `POST /provider-configs` -Create (returns 201)
- `GET /provider-configs` -List with pagination/filtering
- `GET /provider-configs/{id}` -Get by ID
- `PUT /provider-configs/{id}` -Update
- `DELETE /provider-configs/{id}` -Delete (returns 204)

### 1.6 Tenant Management (`core/tenant.py`)

**Dataclass: `TenantConfig`**

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | -| Tenant identifier |
| `name` | `str` | -| Display name |
| `enabled` | `bool` | `True` | Whether tenant is active |
| `max_sessions` | `int` | `100` | Max sessions quota |
| `max_agents` | `int` | `10` | Max agents quota |
| `max_memory_entries` | `int` | `1000` | Max memory entries quota |
| `max_concurrent_runs` | `int` | `4` | Max concurrent agent runs |
| `features` | `dict[str, bool]` | `{}` | Feature flags |
| `allowed_models` | `list[str]` | `[]` | Model whitelist |
| `default_model` | `str` | `""` | Default model |
| `storage_prefix` | `str` | `""` | Storage path prefix |
| `metadata` | `dict[str, Any]` | `{}` | Additional metadata |

**Dataclass: `TenantUsage`** -Tracks `session_count`, `agent_count`, `memory_entry_count`, `active_runs`, `total_tokens_used`, `total_runs`.

**Class: `TenantManager`**

| Method | Signature | Description |
|---|---|---|
| `register` | `(config: TenantConfig) -> None` | Register new tenant |
| `unregister` | `(tenant_id: str) -> bool` | Remove tenant |
| `get` | `(tenant_id: str) -> Optional[TenantConfig]` | Get tenant config |
| `get_or_default` | `(tenant_id) -> TenantConfig` | Get tenant or default config |
| `check_quota` | `(tenant_id: str) -> tuple[bool, str]` | Validate quota compliance |
| `resolve_tenant` | `(session_key, user_id, request_headers) -> str` | Resolve tenant from request |

**Class: `TenantIsolation`** (static methods)

| Method | Description |
|---|---|
| `get_session_prefix(tenant_id)` | Session key prefix for isolation |
| `get_memory_path(tenant_id)` | Tenant-specific memory path |
| `get_session_path(tenant_id)` | Tenant-specific session path |
| `get_auth_path(tenant_id)` | Tenant-specific auth path |
| `isolate_session_key(key, tenant_id)` | Prepend tenant ID to session key |
| `extract_tenant_id(session_key)` | Extract tenant ID from session key |

---

## 2. Auth Module

**Path**: `app/atlasclaw/auth/`

Implements the authentication pipeline: credential extraction ->provider authentication ->DB user resolution ->UserInfo injection.

### 2.1 Data Models (`auth/models.py`)

**Exception: `AuthenticationError`** -Raised when authentication fails.

**Dataclass: `UserInfo`** -Authenticated user identity injected into SkillDeps.

| Field | Type | Default | Description |
|---|---|---|---|
| `user_id` | `str` | -| Internal DB user UUID or `"anonymous"` / `"default"` |
| `display_name` | `str` | `""` | Human-readable name |
| `tenant_id` | `str` | `"default"` | Tenant/org identifier |
| `roles` | `list[str]` | `[]` | Role strings |
| `raw_token` | `str` | `""` | Original auth credential |
| `provider_subject` | `str` | `""` | Composite key `"{provider}:{subject}"` |
| `extra` | `dict[str, Any]` | `{}` | Extension context |
| `auth_type` | `str` | `""` | Auth source type, e.g. `"local"`, `"oidc:keycloak"` |


Properties: `is_anonymous: bool`, `is_default: bool`

**Sentinel**: `ANONYMOUS_USER = UserInfo(user_id="anonymous", display_name="Anonymous")`

**Dataclass: `AuthResult`** -Returned by `AuthProvider.authenticate()`, not persisted.

| Field | Type | Default |
|---|---|---|
| `subject` | `str` | -|
| `display_name` | `str` | `""` |
| `email` | `str` | `""` |
| `roles` | `list[str]` | `[]` |
| `tenant_id` | `str` | `"default"` |
| `raw_token` | `str` | `""` |
| `extra` | `dict[str, Any]` | `{}` |

External auth users are persisted in the DB `users` table. The helper module `auth/user_store.py` resolves an `AuthResult` into a DB user and then builds the request `UserInfo`.

### 2.2 Auth Middleware (`auth/middleware.py`)

**Class: `AuthMiddleware(BaseHTTPMiddleware)`**

Constructor: `strategy: AuthStrategy`, `anonymous_fallback: bool = False`, `oidc_redirect_uri: str = ""`

**Credential Extraction Order**:
1. `Authorization: Bearer <token>` header
2. `AtlasClaw-Authenticate` header
3. Cookie-based tokens (configurable via `jwt.cookie_name`)

**Skip Paths** (no auth required): `/health`, `/ping`, `/favicon.ico`, `/docs`, `/openapi.json`, `/static/`, `/styles/`, `/scripts/`, `/locales/`, `/config.json`, `/index.html`, `/login.html`

**Auth Route Notes**:
- `/api/auth/login`, `/api/auth/local/login`, `/api/auth/callback`, `/api/auth/logout`, and `/api/auth/me` are handled specially by the middleware rather than being unconditional skip paths.
- In `host_cookie` mode, the middleware accepts either a valid AtlasClaw JWT session (for local workspace admin access) or configured embedded host cookies (for host end-user access).
- When `auth.provider="host_cookie"` and `auth.local.enabled=true`, local admin login remains available for backend management surfaces.
- Embedded host cookie mode is configured under `auth.host_cookie`, including the token header/cookie name and the host identity cookie names used for subject, display name, user ID, and tenant ID.


**Function**: `setup_auth_middleware(app, auth_config, workspace_path=".") -> None`

### 2.3 Auth Strategy (`auth/strategy.py`)

**Class: `AuthStrategy`**

Constructor: `providers: list[AuthProvider]`, `workspace_path: str = "."`, `cache_ttl_seconds: int = 300`

| Method | Signature | Description |
|---|---|---|
| `resolve_user` | `(credential: str) -> UserInfo` | Full auth pipeline: check TTL cache ->`provider.authenticate()` ->resolve/create DB user ->convert to `UserInfo` and cache |

**Function**: `create_auth_strategy(config, workspace_path=".") -> Optional[AuthStrategy]`

### 2.4 Auth Providers (`auth/providers/`)

**ABC: `AuthProvider`** (`auth/providers/base.py`)

| Member | Type | Description |
|---|---|---|
| `provider_name` | abstract property | Provider identifier string |
| `authenticate` | `(credential: str) -> AuthResult` | Validate credential and return result |

**Built-in Providers**:

| Provider | File | Description | Key Config |
|---|---|---|---|
| `NoneProvider` | `none.py` | No authentication; returns default user | `default_user_id: str = "default"` |
| `LocalAuthProvider` | `local.py` | Local username/password authentication (DB-backed) | `UserModel.password_hash` (bcrypt) |
| `OIDCJWTProvider` | `oidc_jwt.py` | JWT validation via JWKS endpoint (RS256/RS384/RS512) for API auth | `issuer`, `client_id`, `jwks_uri`, `audience` |
| `OIDCLoginProvider` | `oidc_login.py` | Full OAuth2 OIDC with PKCE (S256) for browser SSO | `issuer`, `client_id`, `client_secret`, `redirect_uri`, `authorization_endpoint`, `token_endpoint`, `userinfo_endpoint` |

**Auth Provider Architecture**:

*Primary Providers* (configured via `auth.provider` for user login):
- `none` -No authentication
- `local` -Username/password against local database
- `host_cookie` -Embedded host cookie authentication for end-user access; may coexist with local admin login when `auth.local.enabled` is enabled

*Secondary Providers* (always available for specific use cases):
- `oidc_jwt` -JWT token validation for API bearer authentication
- `oidc_login` -OAuth2 authorization code flow for browser SSO

*Chained Authentication*: The `AuthStrategy` supports trying multiple providers in sequence (e.g., local auth first, then OIDC JWT).


### 2.5 User Store Helpers (`auth/user_store.py`)

External auth providers resolve into DB-backed `UserModel` records instead of a JSON mapping file.

| Function | Signature | Description |
|---|---|---|
| `ensure_db_user_for_auth_result` | `(session, provider, result, default_roles=None) -> UserModel` | Idempotently resolve/create a DB user by authenticated subject |
| `build_user_info_from_db_user` | `(user, provider, result, raw_token="", extra=None, auth_type="") -> UserInfo` | Convert a DB user and auth result into request identity |
| `extract_role_identifiers` | `(raw_roles) -> list[str]` | Normalize DB role storage into enabled role identifiers |

New external users receive the standard user role by default. Existing DB users keep their assigned roles and active state.

### 2.6 Auth Registry (`auth/registry.py`)

**Class: `AuthRegistry`** (class-level registry)

| Method | Signature | Description |
|---|---|---|
| `register` | `(auth_id: str, provider_class: Type[AuthProvider]) -> None` | Register a provider class |
| `get` | `(auth_id: str) -> Optional[Type[AuthProvider]]` | Get provider class by ID |
| `list_providers` | `() -> list[dict]` | List all registered providers |

### 2.7 Auth Config (`auth/config.py`)

**Dataclass: `OIDCConfig`** -`issuer`, `client_id`, `client_secret`, `jwks_uri`, `scopes: list[str]`, `authorization_endpoint`, `token_endpoint`, `userinfo_endpoint`, `redirect_uri`, `pkce_enabled: bool`, `pkce_method: str`

**Dataclass: `AuthConfig`** -`provider: str`, `cache_ttl_seconds: int`, `oidc: OIDCConfig`

---

## 3. Agent Module

**Path**: `app/atlasclaw/agent/`

Core agent execution engine with streaming, prompt construction, capability-index routing, context compaction, token management, and agent instance pooling.

### 3.1 Agent Runner (`agent/runner.py`)

**Class: `AgentRunner`**

Constructor:
```python
__init__(
    agent: Any,              # pydantic_ai.Agent
    session_manager: SessionManager,
    prompt_builder: Optional[PromptBuilder] = None,
    compaction: Optional[CompactionPipeline] = None,
    hook_system: Optional[HookSystem] = None,
    session_queue: Optional[SessionQueue] = None,
    agent_id: str = "main",
    token_pool: Optional[TokenPool] = None,
    token_policy: Optional[DynamicTokenPolicy] = None,
    agent_pool: Optional[AgentInstancePool] = None,
    token_interceptor: Optional[TokenHealthInterceptor] = None,
    agent_factory: Optional[Callable[[str, TokenEntry], Agent]] = None,
)
```

**Key Method**: `run(session_key, user_message, deps, *, max_tool_calls, timeout_seconds) -> AsyncIterator[StreamEvent]`

**Execution Flow**:
1. Emit `lifecycle_start`
2. **Select/pin token** via `DynamicTokenPolicy.get_or_select_session_token()`
3. **Get or create agent instance** from `AgentInstancePool`
4. Load session and transcript from JSONL
5. Build message history from transcript entries
6. Preserve recent follow-up context and steering messages
7. Build visible capability index from eligible skills/tools
8. Build system prompt via `PromptBuilder.build()`
9. Trigger `BEFORE_PROMPT_BUILD` hook
10. Run `CompactionPipeline` if context exceeds threshold
11. Check `SessionQueue` for steering/follow-up messages
12. Execute `PydanticAI Agent.run_stream()` -model inference and continuation loops
13. **Update token health** via `TokenHealthInterceptor.on_response()`
14. Yield `StreamEvent`s with explicit loop metadata for each model re-entry
15. Persist transcript entries (append to JSONL)
16. Emit `lifecycle_end` (or `lifecycle_aborted`/error)

Enforces `max_tool_calls` and `timeout_seconds` checkpoints.

### 3.2 Stream Events (`agent/stream.py`)

**Enum: `StreamEventType`** -`LIFECYCLE`, `ASSISTANT`, `TOOL`, `ERROR`, `COMPACTION`

**Dataclass: `StreamEvent`**

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `str` | -| Event type |
| `phase` | `str` | `""` | Phase (start/end/error/aborted/update) |
| `content` | `str` | `""` | Content (assistant delta text) |
| `tool` | `str` | `""` | Tool name |
| `error` | `str` | `""` | Error message |
| `metadata` | `dict` | `{}` | Additional metadata, including `loop_index`, `loop_reason`, and `selected_capability_ids` |

**Factory Methods**: `lifecycle_start()`, `lifecycle_end()`, `lifecycle_aborted()`, `assistant_delta(content)`, `tool_start(tool_name)`, `tool_end(tool_name, result)`, `error_event(error)`, `compaction_start()`, `compaction_end()`

**Instance Method**: `to_dict() -> dict` -Serializes to dictionary, omitting empty fields.

**Class: `BlockChunker`** -Streaming text chunker with configurable min/max chars and break preference.

Constructor: `min_chars=800`, `max_chars=1200`, `break_preference=PARAGRAPH`, `idle_ms=300`, `text_chunk_limit=None`

| Method | Signature | Description |
|---|---|---|
| `feed` | `(text: str) -> list[str]` | Feed text, return complete chunks |
| `flush` | `() -> Optional[str]` | Flush remaining buffer |
| `reset` | `() -> None` | Reset buffer state |

**Enum: `BreakPreference`** -`PARAGRAPH`, `NEWLINE`, `SENTENCE`, `WHITESPACE`

**Class: `NoReplyFilter`** -Filters `NO_REPLY` tokens from LLM output. Methods: `should_suppress(text) -> bool`, `filter(text) -> str`.


### 3.3A Tool Necessity Gate Runtime (`agent/tool_gate.py`, `agent/tool_gate_models.py`)

Phase 1 adds a classifier-first runtime policy layer in front of unrestricted model completion.
It is responsible for deciding whether the current turn may answer directly, which runtime
capabilities can satisfy required grounding, and whether the final answer must be blocked.

**Core models**:
- `ToolPolicyMode` - `answer_direct`, `prefer_tool`, `must_use_tool`
- `ToolGateDecision` - normalized classifier output describing whether tools are required
- `ToolCandidate` - concrete matched runtime tool/provider candidate
- `CapabilityMatchResult` - resolved tool candidates plus missing capabilities
- `ToolEnforcementOutcome` - whether the runner blocked the final answer

**Class: `ToolNecessityGate`**

| Method | Signature | Description |
|---|---|---|
| `classify` | `(user_message, recent_history) -> ToolGateDecision` | Returns the neutral default when no classifier decision is available |
| `classify_async` | `(user_message, recent_history, *, classifier=None) -> ToolGateDecision` | Uses an explicit classifier when available, otherwise falls back to `classify()` |

**Class: `CapabilityMatcher`**

| Method | Signature | Description |
|---|---|---|
| `match` | `(suggested_tool_classes) -> CapabilityMatchResult` | Maps abstract capability classes to concrete runtime capabilities visible in the prompt surface |

**Supported capability classes**:
- `web_search`
- `web_fetch`
- `browser`
- `provider:<type>`
- `memory`
- `session`
- `hooks_context`

**Runtime integration**:
- `AgentRunner` calls the gate after the prompt and capability surface are prepared
- explicit injected classifiers may provide the `ToolGateDecision`
- model-backed classification is available as an opt-in runtime capability and is disabled by default
- missing required capabilities produce a blocked final answer instead of an ungrounded reply
- tool-gate and enforcement outcomes are emitted to Hook Runtime events

### 3.3 Prompt Builder (`agent/prompt_builder.py`)

**Dataclass: `PromptBuilderConfig`**

| Field | Default | Description |
|---|---|---|
| `mode` | `PromptMode.FULL` | Prompt construction mode |
| `bootstrap_max_chars` | `20000` | Max bootstrap file size |
| `workspace_path` | -| Workspace root path |
| `user_timezone` | `None` | User timezone |
| `time_format` | `"auto"` | Time format (auto/12/24) |
| `sandbox` | `SandboxConfig()` | Sandbox settings |
| `agent_name` | `"AtlasClaw"` | Agent display name |
| `agent_description` | `"Enterprise AI Assistant"` | Agent description |
| `md_skills_max_index_chars` | `3000` | Max MD skills index size |
| `md_skills_desc_max_chars` | `200` | Max per-skill description size |
| `md_skills_max_count` | `20` | Max MD skills in index |

**Class: `PromptBuilder`**

| Method | Signature | Description |
|---|---|---|
| `build` | `(session, skills, tools) -> str` | Build complete system prompt from the filtered capability index and preserved context |

**Prompt Surface**:
- `Available Capabilities` is rendered from the filtered capability index, not the raw registry.
- Recent follow-up context is preserved by default so phrases like "these", "above", and "that list" can resolve against the active conversation.
- `Routing Contract` asks the model to choose one of `direct_answer`, `ask_clarification`, `use_tools`, or `create_artifact` instead of calling a separate planner.

**Capability Index Entry**:

| Field | Description |
|---|---|
| `capability_id` | Stable identifier used for selection and replay |
| `kind` | `skill` or `tool` |
| `name` | Capability name shown in the prompt |
| `summary` | One-sentence capability summary |
| `provider_type` | Provider scope, if any |
| `artifact_types` | Artifact outputs supported by the capability |
| `declared_tool_names` | Concrete tool names exposed by a skill |
| `input_hints` | Hints that help the model choose the capability |
| `file_path` | `SKILL.md` path for markdown-only skills |

**Bootstrap Files Injected** (in FULL mode): `AGENTS.md`, `SOUL.md`, `TOOLS.md`, `IDENTITY.md`, `USER.md`, `HEARTBEAT.md`, `BOOTSTRAP.md`

**Prompt Modes**:
- `FULL` -Complete runtime prompt with all sections (identity, bootstrap, tools, skills index)
- `MINIMAL` -Sub-agent mode without optional sections
- `NONE` -Only basic identity line

### 3.4 Routing & Agent Config (`agent/routing.py`)

**Enum: `DmScope`** -Session isolation for direct messages.
- `MAIN` -Single shared session
- `PER_PEER` -Per peer session
- `PER_CHANNEL_PEER` -Per channel and peer
- `PER_ACCOUNT_CHANNEL_PEER` -Per account, channel, peer

**Enum: `SandboxMode`** -`OFF`, `AGENT`, `SESSION`

**Dataclass: `ToolPolicy`** -Tool allow/deny filtering.

| Field | Type | Default | Description |
|---|---|---|---|
| `allow` | `list[str]` | `["*"]` | Allow list (supports wildcards) |
| `deny` | `list[str]` | `[]` | Deny list (higher priority than allow) |

Methods: `is_allowed(tool_name: str) -> bool`, `_match_pattern(pattern, name) -> bool` (supports `*`, `prefix*`, `*suffix`)

### 3.5 Agent Definition Loader (`agent/agent_definition.py`)

**Class: `AgentDefinitionParser`** -Parses agent definition markdown files.

| Method | Signature | Description |
|---|---|---|
| `parse_soul_md` | `(content) -> dict` | Extract capabilities and providers from SOUL.md |
| `parse_identity_md` | `(content) -> dict` | Extract personality from IDENTITY.md |
| `parse_user_md` | `(content) -> dict` | Extract interaction patterns from USER.md |
| `parse_memory_md` | `(content) -> dict` | Extract memory strategy from MEMORY.md |
| `parse_frontmatter` | `(content) -> tuple[dict, str]` | Parse YAML frontmatter |

**Class: `AgentLoader`** -Loads and assembles `AgentConfig` from the `agents/<agent_id>/` directory.

| Method | Signature | Description |
|---|---|---|
| `load_agent` | `(agent_id: str) -> AgentConfig` | Load full agent configuration |

### 3.6 Compaction Pipeline (`agent/compaction.py`)

Compresses context when it exceeds the configured token threshold. Triggered automatically by `AgentRunner` before each LLM call if context exceeds `CompactionConfig.context_window - reserve_tokens_floor`.

### 3.7 Token Policy (`agent/token_policy.py`)

Session-level token selection and pinning strategy.

**Class: `DynamicTokenPolicy`**

Constructor: `token_pool: TokenPool`, `strategy: str = "health"`, `primary_token_id: Optional[str] = None`

| Method | Signature | Description |
|---|---|---|
| `select_for_session` | `(session_key, provider?, model?) -> TokenEntry?` | Select and pin token for session |
| `get_session_token` | `(session_key) -> TokenEntry?` | Get pinned token for session |
| `get_or_select_session_token` | `(session_key, provider?, model?) -> TokenEntry?` | Get existing or select new |
| `refresh_session_token` | `(session_key) -> TokenEntry?` | Re-select if current token unhealthy |
| `release_session_token` | `(session_key) -> None` | Release pinned token |

**Token Pinning**: Once a token is selected for a session, it's reused for the entire session unless it becomes unhealthy.

**Primary Token**: If `primary_token_id` is configured, it's always tried first if healthy.

### 3.8 Agent Instance Pool (`agent/agent_pool.py`)

Caches agent instances bound to specific tokens with per-instance concurrency control.

**Dataclass: `AgentInstance`**

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Agent identifier |
| `token_id` | `str` | Bound token ID |
| `agent` | `Any` | PydanticAI Agent instance |
| `concurrency_sem` | `asyncio.Semaphore` | Concurrency limiter |
| `created_at` | `datetime` | Creation timestamp |

**Property**: `cache_key` -Returns `"{agent_id}:{token_id}"`

**Class: `AgentInstancePool`**

Constructor: `max_concurrent_per_instance: int = 4`

| Method | Signature | Description |
|---|---|---|
| `get_or_create` | `(agent_id, token, agent_factory) -> AgentInstance` | Get existing or create new instance |
| `get` | `(agent_id, token_id) -> AgentInstance?` | Get existing instance |

**Cache Key**: `"{agent_id}:{token_id}"` -Each token gets its own agent instance.

**Concurrency**: Each instance has a semaphore limiting concurrent requests (default 4).

---

## 4. API Module

**Path**: `app/atlasclaw/api/`

HTTP routing, SSE streaming, WebSocket gateway, webhook dispatch, and response handling.

### 4.1 Routes & APIContext (`api/routes.py`)

**Dataclass: `APIContext`** -Global context holder for API dependencies.

Holds: `session_manager`, `skill_registry`, `agent_runner`, `provider_registry`, `webhook_manager`, `channel_manager`

Functions: `set_api_context(context: APIContext)`, `get_api_context() -> APIContext`

**Request Models** (Pydantic BaseModel):

| Model | Key Fields |
|---|---|
| `SessionCreateRequest` | `agent_id="main"`, `channel="api"`, `chat_type="dm"`, `scope="main"` |
| `AgentRunRequest` | `session_key`, `message`, `model=None`, `timeout_seconds=600` |
| `SkillExecuteRequest` | `skill_name`, `args={}` |
| `MemorySearchRequest` | `query`, `top_k=10`, `apply_recency=True` |
| `MemoryWriteRequest` | `content`, `memory_type="daily"`, `source=""`, `tags=[]`, `section="General"` |
| `QueueModeRequest` | `mode` (collect/steer/followup/steer-backlog/interrupt) |
| `StatusResponse` | `session_key`, `context_tokens`, `input_tokens`, `output_tokens`, `queue_mode`, `queue_size` |

**Function**: `create_router() -> APIRouter` -Creates the main API router with all endpoints.

**Function**: `install_request_validation_logging(app)` -Adds validation logging middleware.

### 4.2 SSE Manager (`api/sse.py`)

**Enum: `SSEEventType`** -`LIFECYCLE`, `ASSISTANT`, `TOOL`, `COMPACTION`, `ERROR`, `HEARTBEAT`

**Dataclass: `SSEEvent`**

| Field | Type | Description |
|---|---|---|
| `event_type` | `SSEEventType` | Event category |
| `data` | `dict[str, Any]` | Event payload |
| `event_id` | `Optional[str]` | Monotonic ID for `Last-Event-ID` resume |
| `retry` | `Optional[int]` | Reconnect interval in ms |

Method: `to_sse_format() -> dict`

**Dataclass: `StreamState`** -`run_id: str`, `last_event_id: str`, `event_count: int`, `started_at: float`

**Class: `SSEManager`**

| Method | Signature | Description |
|---|---|---|
| `create_stream` | `(run_id: str) -> AsyncIterator[dict]` | Create SSE stream |
| `heartbeat` | `()` | Send heartbeat event |

### 4.3 Gateway (`api/gateway.py`)

WebSocket frame protocol for persistent connections.

**Enum: `FrameType`** -`CONNECT`, `HELLO_OK`, `REQUEST`, `RESPONSE`, `EVENT`

**Enum: `ConnectionState`** -`CONNECTING`, `CONNECTED`, `AUTHENTICATED`, `DISCONNECTING`, `DISCONNECTED`

**Dataclass: `DeviceInfo`** -`device_id`, `platform`, `version`, `user_agent`

**Dataclass: `ConnectionInfo`** -`connection_id`, `device`, `state`, `authenticated_at`, `user_id`, `tenant_id`, `user_info`, `created_at`, `last_activity`

**Frame Dataclasses**: `ConnectFrame`, `HelloOkFrame`, `RequestFrame`, `ResponseFrame`, `EventFrame`

### 4.4 Database API Routes (`api/api_routes.py`)

REST endpoints for configuration management with DB persistence.

**Agent Configs** (`/api/agent-configs`):
- `POST /agent-configs` -Create agent configuration
- `GET /agent-configs` -List with pagination
- `GET /agent-configs/{id}` -Get by ID
- `PUT /agent-configs/{id}` -Update
- `DELETE /agent-configs/{id}` -Delete

**Token Configs** (`/api/token-configs`):
- `POST /token-configs` -Create token (API key encrypted at rest)
- `GET /token-configs` -List with pagination
- `GET /token-configs/{id}` -Get by ID
- `PUT /token-configs/{id}` -Update
- `DELETE /token-configs/{id}` -Delete

**Provider Configs** (`/api/provider-configs`):
- `POST /provider-configs` -Create provider instance config
- `GET /provider-configs` -List with filters (`provider_type`, `is_active`)
- `GET /provider-configs/{id}` -Get by ID
- `PUT /provider-configs/{id}` -Update
- `DELETE /provider-configs/{id}` -Delete

**Users** (`/api/users`):
- `POST /users` -Create user (password hashed with bcrypt)
- `GET /users` -List with pagination
- `GET /users/{id}` -Get by ID
- `PUT /users/{id}` -Update
- `DELETE /users/{id}` -Delete

**Model Configs** (`/api/model-configs`):
- `POST /model-configs` -Create model configuration (returns 201)
- `GET /model-configs` -List with pagination and filtering (`provider`, `is_active`)
- `GET /model-configs/{id}` -Get by ID
- `PUT /model-configs/{id}` -Update
- `DELETE /model-configs/{id}` -Delete (returns 204)

### 4.5 Other API Modules

| File | Purpose |
|---|---|
| `channels.py` | Channel management REST endpoints |
| `agent_info.py` | Agent information endpoints |
| `routes_hooks.py` | Generic hook runtime events, pending items, and decision endpoints |
| `webhook_dispatch.py` | Inbound webhook routing and dispatch |
| `channel_hooks.py` | Channel-specific hook triggers |
| `response_handler.py` | Response normalization across channel types |
| `request_orchestrator.py` | Request routing logic and orchestration |

---

## 5. Channels Module

**Path**: `app/atlasclaw/channels/`

Channel handler abstraction, registry, manager, and built-in handler implementations.

### 5.1 Channel Config & Message Types (`channels/base.py`)

**Dataclass: `ChannelConfig`**

| Field | Type | Default | Description |
|---|---|---|---|
| `channel_id` | `str` | -| Channel identifier |
| `channel_type` | `str` | -| Handler type |
| `text_chunk_limit` | `int` | `4096` | Max text per message |
| `supports_markdown` | `bool` | `True` | Markdown support |
| `supports_html` | `bool` | `False` | HTML support |
| `supports_typing` | `bool` | `True` | Typing indicator support |
| `supports_reactions` | `bool` | `False` | Reaction support |
| `rate_limit_per_second` | `float` | `10.0` | Rate limit |
| `markdown_to_html` | `bool` | `False` | Auto-convert markdown |
| `html_to_markdown` | `bool` | `False` | Auto-convert HTML |
| `max_file_size_mb` | `int` | `50` | Max file upload size |
| `allowed_file_types` | `list[str]` | `[]` | Allowed file types |
| `metadata` | `dict[str, Any]` | `{}` | Additional config |

**Enums** (from `base.py`): `MessageType` (TEXT, IMAGE, AUDIO, VIDEO, FILE, STICKER, LOCATION, CONTACT, TYPING), `DeliveryStatus` (PENDING, SENT, DELIVERED, READ, FAILED)

### 5.2 Channel Data Models (`channels/models.py`)

**Enum: `ChannelMode`** -`INBOUND`, `OUTBOUND`, `BIDIRECTIONAL`

**Enum: `ConnectionStatus`** -`DISCONNECTED`, `CONNECTING`, `CONNECTED`, `ERROR`

| Dataclass | Key Fields |
|---|---|
| `InboundMessage` | `message_id`, `sender_id`, `sender_name`, `chat_id`, `channel_type`, `content`, `content_type="text"`, `thread_id`, `reply_to`, `metadata`, `timestamp` |
| `OutboundMessage` | `chat_id`, `content`, `content_type="text"`, `thread_id`, `reply_to`, `metadata` |
| `SendResult` | `success: bool`, `message_id: Optional[str]`, `error: Optional[str]` |
| `ChannelValidationResult` | `valid: bool`, `errors: list[str]` |
| `ChannelConnection` | `id`, `name`, `channel_type`, `config`, `enabled=True`, `is_default=False`, `runtime_state`, `created_at`, `updated_at` |

### 5.3 Channel Handler (`channels/handler.py`)

**Abstract Class: `ChannelHandler`**

Class attributes: `channel_type: str`, `channel_name: str`, `channel_icon: str`, `channel_mode: ChannelMode`, `supports_long_connection: bool`, `supports_webhook: bool`

| Abstract Method | Signature | Description |
|---|---|---|
| `setup` | `(connection_config) -> bool` | Initialize with config |
| `start` | `(context) -> bool` | Start the handler |
| `stop` | `() -> bool` | Stop the handler |

**Long Connection Methods**: `connect() -> bool`, `disconnect() -> bool`, `set_message_callback(callback)`

**Webhook Methods**: `validate_webhook(signature) -> bool`, `handle_webhook(payload) -> bool`

### 5.4 Channel Manager (`channels/manager.py`)

**Class: `ChannelManager`**

Constructor: `workspace_path: Path`

When a channel connection stores `config.provider_binding`, `ChannelManager` resolves that
`provider_type/instance_name` against the current user's `user_setting.json` provider section and
injects `provider_type`, `provider_instance_name`, `provider_instance`, `provider_instances`, and
`available_providers` into `SkillDeps.extra` for channel-originated agent turns.

| Method | Signature | Description |
|---|---|---|
| `initialize_connection` | `(user_id, channel_type, connection_id) -> bool` | Initialize a connection |
| `set_agent_runner` | `(agent_runner) -> None` | Set the agent runner for message handling |
| `send_message` | `(user_id, channel_type, connection_id, message) -> SendResult` | Send outbound message |
| `receive_message` | `(inbound_message) -> None` | Process inbound message |

### 5.5 Channel Registry (`channels/registry.py`)

**Class: `ChannelRegistry`** (class-level registry)

| Method | Signature | Description |
|---|---|---|
| `register` | `(channel_type: str, handler_class: Type[ChannelHandler])` | Register handler class |
| `get` | `(channel_type: str) -> Optional[Type[ChannelHandler]]` | Get handler class |
| `create_instance` | `(instance_key, channel_type, config) -> Optional[ChannelHandler]` | Create handler instance |
| `list_handlers` | `() -> dict[str, Type[ChannelHandler]]` | List all registered handlers |

### 5.6 Built-in Channel Handlers (`channels/handlers/`)

| Handler | File | Mode | Connection Type | Key Feature |
|---|---|---|---|---|
| `WebSocketHandler` | `websocket.py` | BIDIRECTIONAL | Long connection | Full duplex streaming |
| `RESTHandler` | `rest.py` | INBOUND | Webhook | Request/response |
| `SSEHandler` | `sse.py` | BIDIRECTIONAL | Long connection | Server-sent events |
| `FeishuHandler` | `feishu.py` | BIDIRECTIONAL | Long connection | Multiprocessing for SDK isolation |
| `DingTalkHandler` | `dingtalk.py` | BIDIRECTIONAL | Long connection | Stream mode integration |
| `WeComHandler` | `wecom.py` | BIDIRECTIONAL | Long connection | WeCom (WeChat Work) |

---

## 6. Tools Module

**Path**: `app/atlasclaw/tools/`

Built-in tool definitions, profiles, group management, and registration.

### 6.1 Tool Catalog (`tools/catalog.py`)

**Group Constants**:

| Constant | Tools |
|---|---|
| `GROUP_FS` | None (high-risk filesystem tools removed from built-in profiles) |
| `GROUP_RUNTIME` | None (high-risk runtime tools removed from built-in profiles) |
| `GROUP_WEB` | `web_search`, `web_fetch` |
| `GROUP_MEMORY` | `memory_search`, `memory_get` |
| `GROUP_SESSIONS` | `sessions_list`, `sessions_history`, `sessions_send`, `sessions_spawn`, `subagents`, `session_status` |
| `GROUP_UI` | `browser` |
| `GROUP_PROVIDERS` | `list_provider_instances`, `select_provider_instance` |

**Enum: `ToolProfile`**

| Profile | Tools Included |
|---|---|
| `MINIMAL` | `session_status` only |
| `CODING` | `GROUP_SESSIONS` + `GROUP_MEMORY` + `GROUP_UI` |
| `MESSAGING` | `sessions_list`, `sessions_history`, `sessions_send`, `session_status` |
| `FULL` | All tool groups |

**Class: `ToolCatalog`**

| Method | Signature | Description |
|---|---|---|
| `expand_groups` | `(items: list[str]) -> list[str]` | Expand `group:*` entries to concrete tool names |
| `get_tools_by_profile` | `(profile: str \| ToolProfile) -> list[str]` | Get concrete tools for a profile |
| `filter_tools` | `(tools, allow, deny) -> list[str]` | Apply allow/deny rules; `deny=["*"]` clears all unless `allow` restores |

### 6.2 Tool Registration (`tools/registration.py`)

**Function**: `register_builtin_tools(skill_registry, profile) -> list[str]` -Registers all tools for the specified profile and returns registered tool names.

### 6.3 Built-in Tool Groups

**Filesystem** (`tools/filesystem/`):
- Legacy file tools remain in the source tree for compatibility, but are not registered by default

**Runtime** (`tools/runtime/`):
- Legacy runtime tools remain in the source tree for compatibility, but are not registered by default

**Memory** (`tools/memory/`):
- `search_tool.py` -Search memory entries
- `get_tool.py` -Retrieve memory by ID

**Sessions** (`tools/sessions/`):
- `list_tool.py` -List sessions
- `history_tool.py` -Get session history
- `send_tool.py` -Send message to session
- `spawn_tool.py` -Create new session
- `status_tool.py` -Get session status
- `subagents_tool.py` -Manage sub-agents

**Web** (`tools/web/`):
- `search_tool.py` -Web search integration
- `fetch_tool.py` -Fetch web content

**UI** (`tools/ui/`):
- `browser_tool.py` -Browser automation

**Providers** (`tools/providers/`):
- `instance_tools.py` -List and select provider instances

---

## 7. Session Module

**Path**: `app/atlasclaw/session/`

JSONL-based session persistence with hierarchical key formats, async locking, and queue management.

### 7.1 Session Context (`session/context.py`)

**Enum: `SessionScope`**

| Value | Format | Example |
|---|---|---|
| `MAIN` | `agent:<agentId>:user:<userId>:main` | `agent:main:user:u-a1:main` |
| `PER_PEER` | `agent:<agentId>:user:<userId>:<chatType>:<peerId>` | `agent:main:user:u-a1:dm:user_42` |
| `PER_CHANNEL_PEER` | `agent:<agentId>:user:<userId>:<channel>:<chatType>:<peerId>` | `agent:main:user:u-a1:telegram:dm:user_42` |
| `PER_ACCOUNT_CHANNEL_PEER` | `agent:<agentId>:user:<userId>:<channel>:<accountId>:<chatType>:<peerId>` | `agent:main:user:u-a1:telegram:acc1:dm:user_42` |

**Enum: `ChatType`** -`DM`, `GROUP`, `CHANNEL`, `THREAD`

**Dataclass: `SessionKey`**

| Field | Type | Default |
|---|---|---|
| `agent_id` | `str` | `"main"` |
| `user_id` | `str` | `"default"` |
| `channel` | `str` | `"main"` |
| `account_id` | `str` | `"default"` |
| `chat_type` | `ChatType` | `DM` |
| `peer_id` | `str` | `"default"` |
| `thread_id` | `Optional[str]` | `None` |

Methods: `to_string(scope) -> str`, `from_string(key) -> SessionKey`

**Dataclass: `IdentityLinks`** -Cross-channel identity mapping. Methods: `resolve(provider_peer_id) -> str`, `add_mapping(canonical_id, provider_peer_id)`

**Class: `SessionKeyFactory`** -Creates `SessionKey` instances with identity link resolution.

**Dataclass: `SessionOrigin`** -Source metadata: `label`, `provider`, `from_id`, `to_id`, `account_id`, `thread_id`

**Dataclass: `SessionMetadata`**

| Field | Type | Default |
|---|---|---|
| `session_id` | `str` | UUID |
| `session_key` | `str` | `""` |
| `created_at` | `datetime` | now |
| `updated_at` | `datetime` | now |
| `agent_id` | `str` | `"main"` |
| `channel` | `str` | `"main"` |
| `account_id` | `str` | `"default"` |
| `peer_id` | `str` | `"default"` |
| `display_name` | `Optional[str]` | `None` |
| `origin` | `Optional[SessionOrigin]` | `None` |
| `input_tokens` | `int` | `0` |
| `output_tokens` | `int` | `0` |
| `total_tokens` | `int` | `0` |
| `context_tokens` | `int` | `0` |
| `compaction_count` | `int` | `0` |
| `last_compacted_at` | `Optional[datetime]` | `None` |
| `memory_flushed_this_cycle` | `bool` | `False` |
| `extra` | `dict` | `{}` |

Methods: `to_dict() -> dict`, `from_dict(data) -> SessionMetadata`

**Dataclass: `TranscriptEntry`**

| Field | Type | Default |
|---|---|---|
| `timestamp` | `datetime` | now |
| `role` | `str` | `"user"` (user/assistant/system/tool) |
| `content` | `str` | `""` |
| `tool_calls` | `list[dict]` | `[]` |
| `tool_results` | `list[dict]` | `[]` |
| `metadata` | `dict` | `{}` |

Methods: `to_dict() -> dict`, `from_dict(data) -> TranscriptEntry`

### 7.2 Session Manager (`session/manager.py`)

**Class: `SessionManager`**

Constructor: `workspace_path="."`, `user_id="default"`, `reset_mode=DAILY`, `daily_reset_hour=4`, `idle_reset_minutes=60`

**Storage Layout**:
```text
<workspace>/users/<user_id>/sessions/
|- sessions.json                         # Session metadata index
|- <session_id>.jsonl                    # Main transcript
|- <session_id>-topic-<thread_id>.jsonl  # Thread transcript
\- archive/                              # Archived sessions
```

| Method | Signature | Description |
|---|---|---|
| `get_or_create` | `(session_key: str) -> SessionMetadata` | Get existing or create new session |
| `load_transcript` | `(session_key: str) -> list[TranscriptEntry]` | Load transcript from JSONL |
| `append_transcript` | `(session_key, entry) -> None` | Append entry to JSONL |
| `save_transcript` | `(session_key, entries) -> None` | Overwrite transcript |
| `reset_session` | `(session_key, archive: bool) -> None` | Reset session, optionally archive |
| `delete_session` | `(session_key) -> bool` | Delete session and transcript |
| `list_sessions` | `() -> list[SessionMetadata]` | List all sessions for user |
| `compact_transcript` | `(session_key) -> None` | Compress old entries |
| `update_activity` | `(session_key) -> None` | Update last activity timestamp |

**Reset Modes**: `DAILY` (at `daily_reset_hour`), `IDLE` (after `idle_reset_minutes`), `MANUAL` (explicit only)

**Concurrency**: Per-session `asyncio.Lock` prevents concurrent transcript writes.

### 7.3 Session Queue (`session/queue.py`)

**Enum: `QueueMode`** -`COLLECT`, `STEER`, `FOLLOWUP`, `STEER_BACKLOG`, `INTERRUPT`

**Class: `SessionQueue`**

| Method | Signature | Description |
|---|---|---|
| `put` | `(session_key, message, mode) -> None` | Enqueue message |
| `get` | `(session_key) -> Optional[str]` | Dequeue next message |
| `clear` | `(session_key) -> None` | Clear queue |
| `get_all` | `(session_key) -> list[str]` | Drain all messages |
| `set_mode` | `(session_key, mode) -> None` | Set queue mode |

---

## 8. Memory Module

**Path**: `app/atlasclaw/memory/`

Markdown-backed memory storage with hybrid search (keyword + semantic).

### 8.1 Memory Manager (`memory/manager.py`)

**Enum: `MemoryType`** -`DAILY`, `LONG_TERM`, `EPHEMERAL`

**Dataclass: `MemoryEntry`**

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Stable entry ID |
| `content` | `str` | Memory content |
| `memory_type` | `MemoryType` | Type classification |
| `source` | `str` | Session or agent source |
| `timestamp` | `datetime` | Creation time |
| `tags` | `list[str]` | Tags for categorization |
| `embedding` | `Optional[list[float]]` | Vector embedding |
| `metadata` | `dict[str, Any]` | Additional metadata |

**Class: `MemoryManager`**

Constructor: `workspace`, `memory_dir="memory"`, `long_term_file="MEMORY.md"`, `user_id="default"`, `daily_prefix=""`, `encoding="utf-8"`

**Storage Layout**:
```text
memory/<user_id>/
|- YYYY-MM-DD.md    # Daily memories (date-scoped)
\- MEMORY.md        # Long-term persistent memory
```

| Method | Signature | Description |
|---|---|---|
| `write` | `(entry: MemoryEntry) -> str` | Write memory entry, returns entry ID |
| `read` | `(memory_type: MemoryType) -> list[MemoryEntry]` | Read all entries of type |
| `search` | `(query: str, top_k: int) -> list[MemoryEntry]` | Hybrid search |
| `delete` | `(entry_id: str) -> bool` | Delete entry by ID |
| `parse_memory_file` | `(path: Path) -> list[MemoryEntry]` | Parse Markdown memory file |
| `append_to_daily` | `(content: str, tags: list) -> None` | Append to today's daily file |
| `append_to_long_term` | `(content: str, tags: list) -> None` | Append to MEMORY.md |

### 8.2 Hybrid Search (`memory/hybrid_search.py`)

Combines keyword matching and semantic similarity with configurable weights:
- `vector_weight` (default 0.7) -Embedding similarity score
- `fulltext_weight` (default 0.3) -Keyword match score
- `time_decay_half_life_days` (default 30) -Recency bias

---

## 9. Skills Module

**Path**: `app/atlasclaw/skills/`

Skill registration, loading, Markdown skill discovery, and frontmatter parsing.

### 9.1 Skill Registry (`skills/registry.py`)

**Name Validation**: Pattern `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`, max 64 chars, no consecutive hyphens.

**Dataclass: `SkillMetadata`**

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | -| Validated skill name |
| `description` | `str` | -| Skill description |
| `category` | `str` | `"utility"` | Category classification |
| `requires_auth` | `bool` | `False` | Requires authentication |
| `timeout_seconds` | `int` | `30` | Execution timeout |
| `location` | `str` | `"built-in"` | Source: `built-in` / `user` / `workspace` |
| `provider_type` | `Optional[str]` | `None` | For provider skills |
| `instance_required` | `bool` | `False` | Requires provider instance selection |

**Dataclass: `MdSkillEntry`** -Markdown-based skill.

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Skill name |
| `description` | `str` | Description |
| `file_path` | `Path` | Path to SKILL.md |
| `location` | `str` | Source location |
| `metadata` | `dict[str, Any]` | Parsed frontmatter |

**Class: `SkillRegistry`**

| Method | Signature | Description |
|---|---|---|
| `register` | `(metadata: SkillMetadata, handler: Callable) -> None` | Register Python skill |
| `load_from_directory` | `(path: str, location: str, provider: Optional[str]) -> int` | Load skills from directory |
| `load_md_skills` | `(path: str, location: str) -> list[MdSkillEntry]` | Load Markdown skills |
| `register_to_agent` | `(agent: Agent) -> None` | Register all skills with PydanticAI agent |
| `list_skills` | `() -> list[SkillMetadata]` | List executable skills |
| `list_md_skills` | `() -> list[MdSkillEntry]` | List Markdown skills |
| `get_skill` | `(name: str) -> Optional[SkillMetadata]` | Get skill by name |

**Loading Priority** (highest ->lowest):
1. Workspace skills: `<workspace>/skills/`
2. Workspace provider skills: `<workspace>/providers/<provider>/skills/`
3. Global skills: `~/.atlasclaw/skills/`
4. Built-in skills: `app/atlasclaw/providers/*/skills/`

### 9.2 Frontmatter Parser (`skills/frontmatter.py`)

**Dataclass: `FrontmatterResult`** -`metadata: dict[str, Any]`, `body: str`

**Function**: `parse_frontmatter(content: str) -> FrontmatterResult`

Parses YAML frontmatter from SKILL.md and PROVIDER.md files:

```yaml
---
name: skill-name
description: Brief description
keywords:
  - keyword1
  - keyword2
use_when:
  - "scenario 1"
avoid_when:
  - "scenario 2"
---
```

Features: BOM handling, CRLF ->LF conversion, quote stripping, list parsing (dash-prefix items), comment skipping.

---

## 10. Hooks Module

**Path**: `app/atlasclaw/hooks/`

Extensible hook capabilities split into two layers:
- `hooks/system.py` for legacy phase-based interception
- runtime-first generic hook infrastructure for event dispatch, state persistence, and memory/context sinks

### 10.1 Hook System (`hooks/system.py`)

**Enum: `HookPhase`** -All lifecycle phases:

| Category | Phases |
|---|---|
| **Gateway** | `GATEWAY_START`, `GATEWAY_STOP` |
| **Session** | `SESSION_START`, `SESSION_END` |
| **Agent** | `BEFORE_AGENT_START`, `AGENT_END` |
| **Prompt** | `BEFORE_PROMPT_BUILD`, `AGENT_BOOTSTRAP` |
| **LLM** | `LLM_INPUT`, `LLM_OUTPUT` |
| **Tool** | `BEFORE_TOOL_CALL`, `AFTER_TOOL_CALL`, `TOOL_RESULT_PERSIST` |
| **Message** | `MESSAGE_RECEIVED`, `MESSAGE_SENDING`, `MESSAGE_SENT` |
| **Compaction** | `BEFORE_COMPACTION`, `AFTER_COMPACTION` |

**Enum: `HookExecutionMode`**
- `SEQUENTIAL` -Executed in priority order; handlers can modify context
- `PARALLEL` -Executed concurrently via `asyncio.gather`; observe-only (return values ignored)

**Dataclass: `HookDefinition`**

| Field | Type | Default | Description |
|---|---|---|---|
| `phase` | `HookPhase` | -| Lifecycle phase |
| `handler` | `Callable[[dict], Awaitable[Optional[dict]]]` | -| Async handler |
| `priority` | `int` | `100` | Lower = earlier execution |
| `mode` | `HookExecutionMode` | `SEQUENTIAL` | Execution mode |
| `name` | `str` | `""` | Hook name for identification |

**Class: `HookSystem`**

| Method | Signature | Description |
|---|---|---|
| `register` | `(hook: HookDefinition) -> None` | Register hook (auto-sorted by priority) |
| `unregister` | `(phase: HookPhase, name: str) -> bool` | Remove hook by name |
| `trigger` | `(phase: str \| HookPhase, context: dict) -> dict` | Trigger all hooks for phase |
| `list_hooks` | `(phase: Optional[HookPhase]) -> list[dict]` | List registered hooks |
| `clear` | `(phase: Optional[HookPhase]) -> None` | Clear hooks |

**Execution Order**: Sequential hooks run first (priority-sorted, can modify context), then parallel hooks run concurrently (observe-only).

**Factory Functions**:
- `create_logging_hook(phase, log_prefix) -> HookDefinition` -Creates a logging observer hook
- `create_session_memory_hook() -> HookDefinition` -Creates session memory flush hook (AGENT_END phase)

---

### 10.2 Runtime Models (`hooks/runtime_models.py`)

**Enum: `HookEventType`** -Generic runtime lifecycle events.

| Category | Events |
|---|---|
| **Run** | `RUN_STARTED`, `RUN_COMPLETED`, `RUN_FAILED` |
| **Message** | `MESSAGE_RECEIVED`, `MESSAGE_USER_CORRECTED` |
| **LLM** | `LLM_REQUESTED`, `LLM_COMPLETED`, `LLM_FAILED` |
| **Tool** | `TOOL_STARTED`, `TOOL_COMPLETED`, `TOOL_FAILED` |
| **Memory** | `MEMORY_CONFIRMED`, `MEMORY_REJECTED` |

**Dataclasses / Models**:
- `HookEventEnvelope` -persisted runtime event
- `PendingHookItem` -item awaiting confirmation or rejection
- `HookDecisionRecord` -decision audit trail
- `HookWriteMemoryRequest` -request contract for memory promotion
- `HookContextInjection` -confirmed context payload returned by `ContextSink`

### 10.3 Runtime Store (`hooks/runtime_store.py`)

**Class: `HookStateStore`**

| Method | Signature | Description |
|---|---|---|
| `append_event` | `(module_name, event) -> None` | Append runtime event to `events.jsonl` |
| `list_events` | `(module_name, user_id) -> list[HookEventEnvelope]` | Read module events for a user |
| `append_pending` | `(module_name, item) -> None` | Append pending item to `pending.jsonl` |
| `list_pending` | `(module_name, user_id) -> list[PendingHookItem]` | List unresolved pending items |
| `resolve_pending` | `(module_name, user_id, pending_id, decision, note) -> PendingHookItem` | Mark pending item confirmed/rejected and write decision log |
| `list_decisions` | `(module_name, user_id) -> list[HookDecisionRecord]` | Read decision history |

**Storage layout**:
- `<workspace>/users/<user_id>/hooks/<module_name>/events.jsonl`
- `<workspace>/users/<user_id>/hooks/<module_name>/pending.jsonl`
- `<workspace>/users/<user_id>/hooks/<module_name>/decisions.jsonl`
- `<workspace>/users/<user_id>/hooks/<module_name>/context.jsonl`

### 10.4 Runtime Sinks (`hooks/runtime_sinks.py`)

**Class: `MemorySink`**

| Method | Signature | Description |
|---|---|---|
| `write_confirmed` | `(request: HookWriteMemoryRequest) -> HookMemoryWriteResult` | Persist confirmed hook output to `memory_<timestamp>.md` |

**Class: `ContextSink`**

| Method | Signature | Description |
|---|---|---|
| `add_injection` | `(module_name, user_id, summary, payload?, source_event_ids?) -> HookContextInjection` | Persist an explicit context injection returned by a hook consumer |
| `list_confirmed` | `(module_name, user_id) -> list[HookContextInjection]` | Surface confirmed items for future prompt/context use |

### 10.5 Runtime Dispatcher (`hooks/runtime.py`)

**Class: `HookRuntime`**

| Method | Signature | Description |
|---|---|---|
| `register` | `(module_name, handler, event_types?) -> None` | Register a built-in runtime handler |
| `register_script_handler` | `(definition: HookScriptHandlerDefinition) -> None` | Register a config-driven local command script handler |
| `list_handlers` | `() -> list[HookHandlerDefinition]` | Inspect current registrations |
| `dispatch` | `(event, module_name?) -> None` | Route event to matching handlers |
| `emit` | `(**event_fields) -> HookEventEnvelope` | Construct and dispatch an event envelope |
| `apply_actions` | `(module_name, user_id, source_event_ids, action_batch) -> None` | Apply structured actions returned by a script hook |
| `create_pending_item` | `(module_name, ...) -> PendingHookItem` | Create a generic pending item for confirmation |
| `resolve_pending` | `(module_name, user_id, pending_id, decision, note) -> PendingHookItem` | Resolve pending, emit memory events, and optionally write memory |

### 10.6 Script Hook Runner (`hooks/runtime_script.py`)

**Classes**:

- `HookScriptHandlerDefinition` -runtime-ready local command handler definition
- `HookScriptRunner` -executes local commands, writes envelopes to stdin, parses stdout JSON
- `HookScriptExecutionError` -raised for invalid JSON, non-zero exit, or timeout

**Supported actions**:

- `create_pending`
- `write_memory`
- `add_context`

### 10.7 Built-In Runtime Handlers (`hooks/runtime_builtin.py`)

Phase 1 includes a generic built-in module:
- `runtime-audit`

Responsibilities:
- Persist all emitted runtime events for audit/debug visibility
- Create generic pending items for high-signal events such as `message.user_corrected`,
  `run.failed`, `llm.failed`, and `tool.failed`

This module exists to exercise the generic runtime end-to-end. It is not a
special-case skill engine and does not add domain-specific learning semantics to the core.

### 10.8 Heartbeat Integration

Hook Runtime is a downstream consumer of heartbeat events rather than the heartbeat scheduler.

Heartbeat bridge utilities convert heartbeat outcomes into generic hook runtime events while
preserving available routing metadata such as:

- `session_key`
- `run_id`
- `channel`
- `account_id`

This allows web chat, hook scripts, and future notification consumers to react to heartbeat
results without coupling heartbeat directly to any one delivery channel.

## 10A. Heartbeat Module

**Path**: `app/atlasclaw/heartbeat/`

Provides unified periodic execution for agent-side heartbeat turns and long-lived channel
connection checks.

### 10A.1 Runtime Core

| Component | Purpose |
|---|---|
| `HeartbeatRuntime` | Schedules due jobs, enforces concurrency, persists state, emits events |
| `HeartbeatRuntimeContext` | Injects executors, store, and event-bridge behavior |
| `HeartbeatStateStore` | Persists job definitions, state snapshots, and local heartbeat event logs |
| `HeartbeatTargetResolver` | Resolves delivery targets such as `user_chat`, `group_chat`, `session`, and `thread` |

### 10A.2 Models

| Model / Enum | Purpose |
|---|---|
| `HeartbeatJobType` | `agent_turn` / `channel_connection` |
| `HeartbeatTargetType` | `none`, `last_active`, `user_chat`, `channel_connection`, `group_chat`, `session`, `thread` |
| `HeartbeatDeliveryMode` | Delivery semantics including `summary_only` |
| `HeartbeatEventType` | Heartbeat runtime event taxonomy |
| `HeartbeatJobDefinition` | Normalized heartbeat job definition |
| `HeartbeatJobStateSnapshot` | Durable health and scheduling state |
| `HeartbeatEventEnvelope` | Persisted heartbeat event payload |

### 10A.3 Executors

| Executor | Description |
|---|---|
| `AgentHeartbeatExecutor` | Executes OpenClaw-style heartbeat turns, normalizes `HEARTBEAT_OK`, returns context payloads |
| `ChannelHeartbeatExecutor` | Probes and reconnects long-lived channel connections, tracks quiet retries and degraded state |

### 10A.4 Storage

Per-user heartbeat storage layout:

- `<workspace>/users/<user_id>/heartbeat/jobs.json`
- `<workspace>/users/<user_id>/heartbeat/state.json`
- `<workspace>/users/<user_id>/heartbeat/events.jsonl`

## 11. Database Module

**Path**: `app/atlasclaw/db/`

Relational database abstraction with SQLAlchemy ORM, supporting SQLite (default) and MySQL (enterprise).

### 11.1 Database Configuration (`db/database.py`)

**Dataclass: `DatabaseConfig`**

| Field | Type | Default | Description |
|---|---|---|---|
| `db_type` | `str` | `"sqlite"` | Database type: `sqlite` or `mysql` |
| `sqlite_path` | `Optional[str]` | `None` | SQLite file path (relative or absolute) |
| `mysql_host` | `Optional[str]` | `None` | MySQL host |
| `mysql_port` | `int` | `3306` | MySQL port |
| `mysql_database` | `Optional[str]` | `None` | MySQL database name |
| `mysql_user` | `Optional[str]` | `None` | MySQL username |
| `mysql_password` | `Optional[str]` | `None` | MySQL password |
| `mysql_charset` | `str` | `"utf8mb4"` | MySQL charset |
| `pool_size` | `int` | `5` | Connection pool size |
| `max_overflow` | `int` | `10` | Max overflow connections |
| `echo` | `bool` | `False` | Log SQL statements |

**Class: `DatabaseManager`**

| Method | Signature | Description |
|---|---|---|
| `create_tables` | `() -> None` | Create all tables from ORM models (SQLite mode) |
| `get_session` | `() -> AsyncSession` | Get async SQLAlchemy session |
| `close` | `() -> None` | Close all connections |

**Module Functions**:
- `init_database(config: DatabaseConfig) -> DatabaseManager` -Initialize and return manager
- `get_db_manager() -> DatabaseManager` -Get singleton instance

### 11.2 ORM Models (`db/models.py`)

**Base: `Base`** -SQLAlchemy declarative base.

| Model | Table | Purpose |
|---|---|---|
| `AgentModel` | `agents` | Agent configuration |
| `ModelTokenConfigModel` | `model_token_configs` | LLM token/API keys |
| `UserModel` | `users` | User accounts |
| `ChannelModel` | `channels` | Channel configurations |
| `ServiceProviderConfigModel` | `service_provider_configs` | Provider instance configs |
| `ModelConfigModel` | `model_configs` | AI model configurations |

**Common Fields**:
- `id: str` -UUID primary key
- `created_at: datetime` -Creation timestamp
- `updated_at: datetime` -Last update timestamp

### 11.3 ORM Services (`db/orm/`)

Each model has a corresponding service class for CRUD operations:

**AgentConfigService** (`db/orm/agent_config.py`):
- `create(session, data: AgentCreate) -> AgentModel`
- `get_by_id(session, id) -> Optional[AgentModel]`
- `get_by_name(session, name) -> Optional[AgentModel]`
- `list_all(session, page, page_size) -> tuple[list[AgentModel], int]`
- `update(session, id, data: AgentUpdate) -> Optional[AgentModel]`
- `delete(session, id) -> bool`

**ModelTokenConfigService** (`db/orm/model_token_config.py`):

All API keys are encrypted at rest using AES-256-GCM. The service provides transparent encryption/decryption:

- `create(session, data: TokenCreate) -> ModelTokenConfigModel` -Encrypts `api_key` before storage
- `get_by_id(session, id) -> Optional[ModelTokenConfigModel]`
- `get_by_name(session, name) -> Optional[ModelTokenConfigModel]`
- `list_all(session, is_active, page, page_size) -> tuple[list[ModelTokenConfigModel], int]`
- `update(session, id, data: TokenUpdate) -> Optional[ModelTokenConfigModel]` -Re-encrypts if `api_key` changed
- `delete(session, id) -> bool`
- `get_decrypted_api_key(token) -> str` -Decrypt API key for runtime use
- `get_masked_api_key(token) -> str` -Return masked version (e.g., `***d8f`) for display

**Encryption Details**:
- Algorithm: AES-256-GCM
- Key: Configurable via `ATLASCLAW_ENCRYPTION_KEY` env var (32-byte base64)
- Default: Built-in key for development (not for production)
- Format: `v1:key_id:base64(nonce+ciphertext+tag)`
- Backward compatible: Legacy unencrypted values are read as-is

**ServiceProviderConfigService** (`db/orm/service_provider_config.py`):

Provider configurations are encrypted at rest. The `config` JSON field is automatically encrypted before storage and decrypted when accessed:

- `create(session, data: ServiceProviderConfigCreate) -> ServiceProviderConfigModel`
- `get_by_id(session, id) -> Optional[ServiceProviderConfigModel]`
- `get_by_provider_instance(session, provider_type, instance_name) -> Optional[ServiceProviderConfigModel]`
- `list_all(session, provider_type, is_active, page, page_size) -> tuple[list[ServiceProviderConfigModel], int]`
- `list_active_as_nested(session) -> dict[str, dict[str, dict]]` -Return nested dict for merge (decrypted)
- `update(session, id, data: ServiceProviderConfigUpdate) -> Optional[ServiceProviderConfigModel]`
- `delete(session, id) -> bool`

**UserService** (`db/orm/user.py`):
- `create(session, data: UserCreate) -> UserModel`
- `get_by_id(session, id) -> Optional[UserModel]`
- `get_by_username(session, username) -> Optional[UserModel]`
- `list_all(session, page, page_size) -> tuple[list[UserModel], int]`
- `update(session, id, data: UserUpdate) -> Optional[UserModel]`
- `delete(session, id) -> bool`
- `verify_password(password, hash) -> bool` -Verify bcrypt password

**ModelConfigService** (`db/orm/model_config.py`):
- `create(session, data: ModelConfigCreate) -> ModelConfigModel`
- `get_by_id(session, id) -> Optional[ModelConfigModel]`
- `get_by_name(session, name) -> Optional[ModelConfigModel]`
- `list_all(session, provider, is_active, page, page_size) -> tuple[list[ModelConfigModel], int]`
- `list_active(session) -> list[ModelConfigModel]` -List active configs sorted by priority
- `update(session, id, data: ModelConfigUpdate) -> Optional[ModelConfigModel]`
- `delete(session, id) -> bool`
- `get_masked_api_key(model) -> str` -Mask API key for display
- `get_decrypted_api_key(model) -> str` -Decrypt API key

### 11.4 Pydantic Schemas (`db/schemas.py`)

Request/response schemas for API validation:

| Schema | Purpose |
|---|---|
| `AgentCreate/AgentUpdate/AgentResponse` | Agent CRUD |
| `TokenCreate/TokenUpdate/TokenResponse` | Token CRUD |
| `ServiceProviderConfigCreate/Update/Response` | Provider config CRUD |
| `UserCreate/UserUpdate/UserResponse` | User CRUD |
| `ChannelCreate/ChannelUpdate/ChannelResponse` | Channel CRUD |
| `ModelConfigCreate/ModelConfigUpdate/ModelConfigResponse` | Model config CRUD |

### 11.5 Migrations (`migrations/`)

**Alembic Configuration** (`alembic.ini`):
- SQLite: Auto-create via ORM (no migrations needed)
- MySQL: Requires `alembic upgrade head` on startup

**Migration Files** (`migrations/versions/`):
- `001_initial.py` -Initial schema (agents, tokens, users, channels)
- `002_add_channel_is_default.py` -Add `is_default` to channels
- `003_add_service_provider_configs.py` -Add `service_provider_configs` table
- `005_add_model_configs.py` -Add `model_configs` table

---

## 12. Models Module

**Path**: `app/atlasclaw/models/`

LLM provider configuration and failover/retry logic.

### 11.1 Provider Configuration (`models/providers.py`)

**Dataclass: `ModelDefinition`**

| Field | Type | Default |
|---|---|---|
| `id` | `str` | -|
| `name` | `str` | -|
| `context_window` | `int` | `200000` |
| `max_tokens` | `int` | `8192` |
| `reasoning` | `bool` | `False` |

**Dataclass: `ProviderConfig`** -`provider`, `base_url`, `api_key`, `api_type` (openai/anthropic), `models: list[ModelDefinition]`

**Functions**:
- `resolve_env(value: str) -> str` -Resolve `${VAR}` or `${VAR:default}` patterns
- `mask_api_key(key: str) -> str` -Mask to `***abc`

### 11.2 Failover & Retry (`models/failover.py`)

**Enum: `CredentialType`** -`API_KEY`, `OAUTH`, `ACCESS_KEY`

**Dataclass: `AuthProfile`** -`profile_id`, `provider`, `credential_type`, `credential`, `cooldown_until`, `cooldown_count`, `disabled_until`

**Dataclass: `ModelFailoverConfig`** -`primary_model`, `fallbacks: list[str]`, `cooldown_sequence: list[timedelta]`, `billing_initial_cooldown`, `billing_max_cooldown`

---

## 12. Messages Module

**Path**: `app/atlasclaw/messages/`

Inbound/outbound message normalization and slash command parsing.

### 12.1 Message Handling (`messages/handler.py`)

**Enum: `MessageType`** -`TEXT`, `MEDIA`, `ATTACHMENT`, `COMMAND`, `SYSTEM`

**Enum: `ChatType`** -`DM`, `GROUP`, `THREAD`

**Dataclass: `InboundMessage`**

| Field | Type | Description |
|---|---|---|
| `message_id` | `str` | Unique message ID |
| `channel` | `str` | Source channel |
| `account_id` | `str` | Account identifier |
| `peer_id` | `str` | Peer identifier |
| `chat_type` | `ChatType` | Conversation type |
| `body` | `str` | Normalized message text |
| `raw_body` | `str` | Original content |
| `command` | `Optional[ParsedCommand]` | Parsed command if applicable |
| `sender_name` | `str` | Sender display name |
| `media_path` | `str` | Media file path |
| `media_type` | `str` | Media MIME type |
| `reply_to_id` | `str` | Reply-to message ID |
| `timestamp` | `datetime` | Message timestamp |
| `metadata` | `dict` | Additional metadata |

Properties: `is_group_chat`, `is_command`, `has_media`

**Dataclass: `OutboundMessage`** -`body`, `channel`, `account_id`, `peer_id`, `reply_to_id`, `is_chunk`, `chunk_index`, `is_final`, `metadata`

### 12.2 Command Parsing (`messages/command.py`)

**Pattern**: `^/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?$`

**Enum: `CommandCategory`** -`SESSION_CONTROL`, `MODE_SWITCH`, `INFO_QUERY`, `SESSION_MANAGEMENT`, `DEBUG`

**Dataclass: `CommandDefinition`** -`name`, `category`, `description`, `aliases: list[str]`, `requires_llm: bool`, `bypass_debounce: bool`, `strip_from_message: bool`, `handler: Optional[Callable]`

**Dataclass: `ParsedCommand`** -`command`, `name`, `args: list[str]`, `kwargs: dict`, `remaining_text`, `is_standalone`, `definition`

**Class: `CommandParser`**

| Method | Signature | Description |
|---|---|---|
| `register` | `(definition: CommandDefinition) -> None` | Register command |
| `parse` | `(text: str) -> ParsedCommand` | Parse text for slash commands |

---

## 13. Workflow Module

**Path**: `app/atlasclaw/workflow/`

DAG-based workflow engine with conditional branching.

### 13.1 Workflow Engine (`workflow/engine.py`)

**Enum: `StepStatus`** -`PENDING`, `RUNNING`, `COMPLETED`, `SKIPPED`, `FAILED`

**Dataclass: `WorkflowStep`**

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Step identifier |
| `handler` | `StepHandler` | Async step function |
| `after` | `list[str]` | Dependency step names |
| `router` | `Optional[RouterFunc]` | Conditional branching function |
| `status` | `StepStatus` | Current status |
| `result` | `Optional[Any]` | Step result |
| `error` | `Optional[str]` | Error message |

**Class: `WorkflowEngine[T]`** (generic over state type)

| Method | Signature | Description |
|---|---|---|
| `step` | `(after: list[str]) -> Decorator` | Register a workflow step |
| `router` | `(after: list[str]) -> Decorator` | Register a conditional router |
| `run` | `(initial_state: T) -> T` | Execute the workflow DAG |

---

## 14. Media Module

**Path**: `app/atlasclaw/media/`

Media processing utilities.

| File | Purpose |
|---|---|
| `link_extractor.py` | Extract URLs from text content |
| `tts.py` | Text-to-speech integration |
| `understanding.py` | Media content understanding (image/audio analysis) |

---

## 15. Frontend

**Path**: `app/frontend/`

Vanilla JavaScript SPA with Deep Chat integration for the chat interface.

### 15.1 Build System

**Build tool**: esbuild (ES modules, `"type": "module"` in package.json)

| Command | Description |
|---|---|
| `npm run build` | Production build (minified) |
| `npm run build:dev` | Development build (with sourcemap) |
| `npm test` | Run Jest tests |

### 15.2 Key Scripts

| File | Purpose |
|---|---|
| `main.js` | Application entry point, initialization |
| `api-client.js` | HTTP client wrapper for backend API |
| `channel-manager.js` | Channel connection lifecycle management |
| `chat-ui.js` | Chat UI components and rendering |
| `config.js` | Frontend configuration loading |
| `i18n.js` | Internationalization (zh-CN, en-US) |
| `session-manager.js` | Session lifecycle management |
| `stream-handler.js` | SSE stream handling and event processing |

### 15.3 HTML Pages

| Page | Purpose |
|---|---|
| `index.html` | Main chat interface (Deep Chat component) |
| `channels.html` | Channel configuration management UI |

### 15.4 Testing

**Framework**: Jest with jsdom environment. Tests in `tests/frontend/**/*.test.js`.

---

> **Cross-Reference Index**
>
> | Topic | Document | Section |
> |---|---|---|
> | System architecture overview | [Architecture](./ARCHITECTURE.MD) | Section 2 High-Level Architecture |
> | Bootstrap sequence | [Architecture](./ARCHITECTURE.MD) | Section 4 Application Bootstrap |
> | Request lifecycle | [Architecture](./ARCHITECTURE.MD) | Section 5 Request Lifecycle |
> | Security model | [Architecture](./ARCHITECTURE.MD) | Section 8 Security Model |
> | Extension points | [Architecture](./ARCHITECTURE.MD) | Section 9 Extension Points |
> | Code style rules | [Development Spec](./DEVELOPMENT-SPEC.MD) | Section 1 Code Style |
> | Architecture patterns | [Development Spec](./DEVELOPMENT-SPEC.MD) | Section 2 Architecture Patterns |
> | Testing standards | [Development Spec](./DEVELOPMENT-SPEC.MD) | Section 6 Testing |
> | Provider creation | [Provider Guide](./PROVIDER-GUIDE.MD) | Full document |
> | Skill creation | [Skill Guide](./SKILL-GUIDE.MD) | Full document |
> | Channel integration | [Channel Guide](./CHANNEL-GUIDE.MD) | Full document |
