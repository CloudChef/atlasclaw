# AtlasClaw Architecture

> **Canonical architecture reference for the AtlasClaw enterprise agent framework.**
> All future development and feature changes MUST consult this document.
>
> Related documents:
> - [Module Details](./MODULE-DETAILS.MD) -per-module API surface and internals
> - [Development Specification](./DEVELOPMENT-SPEC.MD) -code standards, patterns, and review checklist

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Application Bootstrap & Lifespan](#4-application-bootstrap--lifespan)
5. [Request Lifecycle](#5-request-lifecycle)
6. [Subsystem Interconnections](#6-subsystem-interconnections)
7. [Configuration Architecture](#7-configuration-architecture)
8. [Security Model](#8-security-model)
9. [Extension Points](#9-extension-points)
10. [Streaming & Real-Time Communication](#10-streaming--real-time-communication)
11. [Data Storage Model](#11-data-storage-model)
12. [Technology Stack](#12-technology-stack)

---

## 1. Design Philosophy

AtlasClaw is built on five core design principles that govern all architectural decisions:

### 1.1 Thin Core, Rich Providers

The framework core handles routing, lifecycle, and coordination. All domain-specific logic lives in **providers** -modular packages that encapsulate authentication, skills, and configuration for external systems (ITSM, CRM, monitoring, communication platforms). The core never contains business logic for specific integrations.

### 1.2 Upstream Permission Inheritance With Workspace RBAC

AtlasClaw enforces two complementary authorization layers:

- **Workspace management RBAC**: AtlasClaw maintains role definitions for workspace-scoped administration surfaces such as user management, role management, permission governance, and other configuration catalogs.
- **Upstream permission inheritance**: Runtime agent actions still inherit the authenticated user's upstream permissions. If a user cannot perform an action in the connected enterprise system, the agent cannot perform it on their behalf. This is enforced through `SkillDeps.user_info` propagation to every skill and tool invocation.

### 1.3 Async-First

All I/O-bound operations are async. The framework uses `asyncio.Event` for cancellation/coordination signals, async iterators for streaming, and `asyncio.Lock` for per-session concurrency control. Blocking operations are not permitted in the request path.

### 1.4 Hybrid Storage Model

AtlasClaw uses a hybrid storage approach combining file-system and relational database:

**File-System Storage** (lightweight/embedded mode):
- **Sessions**: JSONL (one JSON object per line, append-only)
- **Memory**: Markdown files (daily date-scoped + long-term)
- **Configuration**: JSON/YAML files with environment variable interpolation
- **Runtime directories**: per-user session and memory files under the workspace

**Relational Database** (enterprise mode):
- **SQLite** (default): Zero-config, file-based, auto-creates schema via ORM on startup
- **MySQL** (enterprise): Requires Alembic migrations for DDL/DML management
- **Users**: DB-backed `users` table for local and external identities

**Database Configuration** (`atlasclaw.json`):
```json
{
  "database": {
    "type": "sqlite",
    "sqlite": { "path": "./data/atlasclaw.db" },
    "mysql": {
      "host": "${MYSQL_HOST}",
      "port": 3306,
      "database": "${MYSQL_DATABASE}",
      "user": "${MYSQL_USER}",
      "password": "${MYSQL_PASSWORD}",
      "charset": "utf8mb4"
    }
  }
}
```

**Key Design Principles**:
- SQLite: No manual SQL needed; schema controlled by ORM models
- MySQL: Enterprise deployments use Alembic for migration control
- Provider configs merge: JSON config + DB entries, with DB taking precedence on same `(provider_type, instance_name)`

### 1.5 Multi-Channel Access

A single agent instance serves multiple access channels simultaneously (REST API, WebSocket, SSE, Feishu, DingTalk, WeCom). Channel handlers normalize inbound messages into a common `InboundMessage` format and translate outbound responses back to channel-specific formats. The agent engine is channel-agnostic.

---

## 2. High-Level Architecture

```text
[Access Channels]
WebSocket | SSE | REST | Feishu | DingTalk | WeCom
    |
    v
[API Layer]
routes.py | gateway.py | sse.py | webhook_dispatch.py
    |
    v
[Auth Middleware]
credential extraction -> provider.authenticate() -> DB user resolution -> UserInfo
    |
    v
[Agent Engine]
AgentRunner | PromptBuilder | CapabilityIndex | CompactionPipeline | PydanticAI Agent
TokenPool | DynamicTokenPolicy | AgentInstancePool | TokenHealthStore
    |
    v
[Runtime Extensions]
Tools | Skills | Hooks
    |
    v
[Core Infrastructure]
ConfigManager | SkillDeps | WorkspaceInitializer | TenantManager | ServiceProviderRegistry
    |
    v
[Data & State Management]
SessionManager (JSONL) | MemoryManager (Markdown) | UserService (DB) | ChannelStore (JSON)
    |
    v
[External LLM Providers]
DeepSeek | OpenAI | Doubao | Kimi | Anthropic
```

### Layer Responsibilities
| Layer | Responsibility | Key Modules |
|---|---|---|
| **Access Channels** | Protocol adaptation, message normalization | `channels/handlers/*` |
| **API Layer** | HTTP routing, SSE streaming, WebSocket frames, webhook dispatch | `api/routes.py`, `api/sse.py`, `api/gateway.py` |
| **Auth Middleware** | Credential extraction, provider authentication, DB user resolution | `auth/middleware.py`, `auth/strategy.py` |
| **Agent Engine** | LLM orchestration, prompt construction, streaming, context compaction, token management, agent instance pooling | `agent/runner.py`, `agent/prompt_builder.py`, `agent/token_policy.py`, `agent/agent_pool.py`, `core/token_pool.py`, `core/token_health_store.py`, `core/token_interceptor.py` |
| **Tools & Skills** | Executable capabilities exposed to the LLM | `tools/`, `skills/` |
| **Hooks** | Lifecycle interception plus generic runtime event dispatch, per-user hook state, and memory/context sinks | `hooks/system.py`, `hooks/runtime.py` |
| **Core Infrastructure** | Configuration, dependency injection, workspace, tenancy | `core/` |
| **Data & State** | Persistence for sessions, memory, users, channels, token health | `session/`, `memory/`, `db/orm/user.py`, `auth/user_store.py` |

### Session Scope Model

AtlasClaw uses a canonical `SessionKey` to separate session ownership from conversation scope:

- `user_id`: the AtlasClaw user that owns the persisted session bucket
- `channel` + `account_id`: the ingress surface and concrete channel connection
- `chat_type` + `peer_id`: the external conversation scope
- `thread_id`: an optional independent thread inside the same scope

Runtime rules:

- Web chat creates independent threads through `POST /api/sessions/threads`
- Channel direct messages are isolated by external sender
- Channel group chats are shared by group or conversation ID
- Metadata and transcripts persist under `workspace/users/<user_id>/sessions/`

---

## 3. Directory Structure

```text
app/atlasclaw/
|- main.py                        # FastAPI application factory + lifespan
|- core/
|  |- config.py                  # ConfigManager (6-level priority chain)
|  |- config_schema.py           # Pydantic config models + enums
|  |- deps.py                    # SkillDeps dataclass (request-scoped DI)
|  |- workspace.py               # Workspace initialization helpers
|  |- provider_registry.py       # ServiceProviderRegistry
|  |- provider_scanner.py        # ProviderScanner (filesystem discovery)
|  |- tenant.py                  # TenantManager, TenantConfig, TenantIsolation
|  |- token_pool.py              # TokenPool, TokenEntry, TokenHealth
|  \- token_interceptor.py      # TokenHealthInterceptor
|- auth/
|  |- models.py                  # UserInfo, AuthResult
|  |- middleware.py              # AuthMiddleware
|  |- strategy.py                # AuthStrategy
|  |- registry.py                # AuthRegistry
|  |- user_store.py              # External auth to DB user helpers
|  |- config.py                  # AuthConfig, OIDCConfig
|  \- providers/
|     |- base.py
|     |- none.py
|     |- local.py
|     |- oidc_jwt.py
|     \- oidc_login.py
|- agent/
|  |- runner.py                  # AgentRunner
|  |- stream.py                  # StreamEvent pipeline
|  |- prompt_builder.py          # FULL / MINIMAL / NONE prompt modes
|  |- routing.py                 # AgentConfig, DmScope, ToolPolicy
|  |- agent_definition.py        # AgentLoader, AgentDefinitionParser
|  |- compaction.py              # CompactionPipeline
|  |- token_policy.py            # DynamicTokenPolicy
|  \- agent_pool.py             # AgentInstancePool
|- api/
|  |- routes.py                  # REST endpoints, APIContext
|  |- gateway.py                 # Gateway frames, ConnectionInfo
|  |- sse.py                     # SSEManager, SSEEvent
|  |- channels.py                # Channel management endpoints
|  |- agent_info.py              # Agent info endpoints
|  |- webhook_dispatch.py        # Webhook routing
|  |- response_handler.py        # Response normalization
|  \- request_orchestrator.py    # Request routing logic
|- channels/
|  |- base.py
|  |- handler.py
|  |- models.py
|  |- manager.py
|  |- registry.py
|  |- store.py
|  \- handlers/
|     |- websocket.py
|     |- rest.py
|     |- sse.py
|     |- feishu.py
|     |- dingtalk.py
|     \- wecom.py
|- tools/
|  |- catalog.py                 # ToolProfile, ToolCatalog, tool groups
|  |- registration.py            # register_builtin_tools()
|  |- base.py
|  |- filesystem/                # Legacy file tools (disabled by default)
|  |- runtime/                   # Legacy runtime tools (disabled by default)
|  |- memory/                    # memory_search, memory_get
|  |- sessions/                  # list, history, send, spawn, status
|  |- web/                       # web_search, web_fetch
|  |- ui/                        # browser tooling
|  \- providers/                 # provider instance tools
|- skills/
|  |- registry.py                # SkillRegistry, SkillMetadata, MdSkillEntry
|  |- frontmatter.py             # parse_frontmatter(), FrontmatterResult
|  \- loader.py                  # SkillLoader
|- session/
|  |- context.py                 # SessionKey, SessionScope, SessionMetadata
|  |- manager.py                 # SessionManager
|  \- queue.py                   # SessionQueue
|- memory/
|  |- manager.py                 # MemoryManager, MemoryEntry, MemoryType
|  \- hybrid_search.py           # Hybrid search
|- models/
|  |- providers.py               # ProviderConfig, ModelDefinition
|  \- failover.py                # AuthProfile, ModelFailoverConfig
|- hooks/
|  \- system.py                  # HookSystem, HookPhase, HookDefinition
|- workflow/
|  |- engine.py                  # WorkflowEngine, WorkflowStep, StepStatus
|  \- orchestrator.py            # Orchestration logic
|- messages/
|  |- handler.py                 # InboundMessage, OutboundMessage, MessageType
|  \- command.py                 # CommandParser, ParsedCommand, CommandDefinition
|- media/
|  |- link_extractor.py          # URL extraction from text
|  |- tts.py                     # Text-to-speech
|  \- understanding.py           # Media content understanding

app/frontend/
|- index.html                     # Main chat interface
|- channels.html                  # Channel management UI
|- package.json                   # esbuild-based build
|- scripts/
|  |- main.js                     # Application entry point
|  |- api-client.js               # HTTP client wrapper
|  |- channel-manager.js          # Channel connection management
|  |- chat-ui.js                  # Chat UI components
|  |- config.js                   # Frontend configuration
|  |- i18n.js                     # Internationalization
|  |- session-manager.js          # Session lifecycle
|  \- stream-handler.js          # SSE stream handling
|- styles/                        # CSS stylesheets
|- static/                        # Static assets
\- locales/                       # i18n translation files

tests/
|- atlasclaw/                     # Python test suite
|  |- conftest.py                 # Shared fixtures
|  |- test_agent.py               # Agent engine tests
|  |- test_config.py              # Configuration tests
|  |- test_session.py             # Session management tests
|  \- ...
\- frontend/                      # JavaScript tests

<workspace>/                       # Runtime workspace (default: ./.atlasclaw)
|- agents/
|  \- main/
|     |- SOUL.md
|     |- IDENTITY.md
|     |- USER.md
|     \- MEMORY.md
\- users/
   \- <user_id>/
      |- user_setting.json         # User-specific config (channels, preferences)
      |- sessions/                 # JSONL session transcripts
      \- memory/                  # Markdown memory files
```

Concrete providers are external packages discovered through `providers_root`.
This repository documents runtime code, contracts, and loading behavior, not
provider implementations.

---

## 4. Application Bootstrap & Lifespan

The application starts via `app/atlasclaw/main.py`:

```
create_app() ->FastAPI(lifespan=lifespan)
```

### Initialization Sequence (Startup)

The `lifespan()` async context manager executes this ordered sequence:

1. **Load configuration** -`ConfigManager.load()` reads `atlasclaw.json`/`atlasclaw.yaml` with environment variable interpolation
2. **Initialize workspace** -`WorkspaceInitializer.initialize()` creates the `.atlasclaw/` directory tree and copies the default `agents/main/` files from the repo template directory when they are missing
3. **Register built-in channel handlers** -Feishu, DingTalk, WeCom handlers registered in `ChannelRegistry`
4. **Initialize ChannelManager** -Creates `ChannelManager` with workspace path
5. **Scan providers** -`ProviderScanner` walks the providers directory (outside workspace), discovers auth and channel extensions
6. **Load agent definitions** -`AgentLoader` reads `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md` from `agents/main/`
7. **Initialize TokenPool** -Creates `TokenPool` from `model.tokens` config, restores health state from `TokenHealthStore`
8. **Initialize SessionManager** -Creates session manager with configured reset mode
9. **Initialize Database** -SQLite (auto-create via ORM) or MySQL (Alembic migrations)
10. **Load TokenPool** -Creates `TokenPool` from merged JSON + DB token configs (DB takes precedence on same token_id)
11. **Load ServiceProviderRegistry** -Reads provider templates from disk, merges JSON + DB instance configs (DB takes precedence on same provider_type.instance_name)
10. **Register built-in tools** -`register_builtin_tools()` with the configured `ToolProfile`
11. **Load skills** -Register built-in executable tools, then load provider markdown skills only for providers with configured instances and standalone markdown skills only when explicitly listed in `skills.enabled_skills`
12. **Create AgentInstancePool** -Creates pool for agent instance caching with concurrency control
13. **Create PydanticAI Agent** -Instantiates the agent with resolved model configuration
14. **Register skills to agent** -`SkillRegistry.register_to_agent()`
15. **Create AgentRunner** -With agent, session manager, prompt builder, compaction pipeline, hook system, session queue, token policy, agent pool, token interceptor
16. **Set up APIContext** -Global context holder for all API routes
17. **Start channel connections** -Initialize active channel connections

### Shutdown Sequence

1. Stop all active channel connections
2. Flush pending session writes
3. Close HTTP clients
4. Cleanup temporary resources

### Global Singletons

These objects are created once during startup and accessed globally:

| Singleton | Access Pattern |
|---|---|
| `ConfigManager` | `get_config_manager()` / `get_config()` |
| `APIContext` | `get_api_context()` |
| `ServiceProviderRegistry` | Via `APIContext` |
| `SessionManager` | Via `APIContext` |
| `SkillRegistry` | Via `APIContext` |
| `AgentRunner` | Via `APIContext` |
| `ChannelManager` | Via `APIContext` |
| `TokenPool` | Via `AgentRunner.token_pool` |
| `TokenHealthStore` | Via `AgentRunner.health_store` |
| `AgentInstancePool` | Via `AgentRunner.agent_pool` |

---

## 5. Request Lifecycle

### 5.1 REST/SSE Agent Run Request

```text
HTTP Request (POST /api/agent/run)
1. AuthMiddleware.dispatch()
   - Extract credential (header -> cookie -> bearer)
   - AuthStrategy.resolve_user(credential)
   - Check TTL cache (300s default)
   - AuthProvider.authenticate(credential) -> AuthResult
   - ensure_db_user_for_auth_result() -> DB User -> UserInfo
   - Attach UserInfo to request.state.user
2. Route handler (api/routes.py)
   - Parse AgentRunRequest (session_key, message, model, timeout)
   - Resolve session key via SessionScope
   - Build SkillDeps (user_info, session, memory managers, abort_signal)
3. AgentRunner.run() -> AsyncIterator[StreamEvent]
   - Emit lifecycle_start
   - Select/pin token via DynamicTokenPolicy.get_or_select_session_token()
   - Get or create agent instance from AgentInstancePool
   - Load session transcript (JSONL)
   - Build message history from transcript
   - Preserve recent follow-up context and steering messages
   - Build visible capability index from eligible skills/tools
   - PromptBuilder.build() -> system prompt with capability surface
   - Trigger BEFORE_PROMPT_BUILD hook
   - Run CompactionPipeline if context exceeds threshold
   - Check SessionQueue for steering/follow-up messages
   - PydanticAI Agent.run_stream() -> model inference
   - Emit explicit loop metadata for each model re-entry
   - Yield assistant deltas and execute tool calls
   - Update TokenHealth via TokenHealthInterceptor.on_response()
   - Enforce max_tool_calls and timeout_seconds
   - Persist transcript entries (append to JSONL)
   - Emit lifecycle_end
4. SSE response
   - event: lifecycle, data: {phase: "start", metadata: {loop_index: 0, loop_reason: "initial_request", selected_capability_ids: []}}
   - event: assistant, data: {content: "delta text", metadata: {loop_index: 0}}
   - event: tool, data: {phase: "start", tool: "web_search", metadata: {selected_capability_ids: ["tool:web_search"]}}
   - event: tool, data: {phase: "end", tool: "web_search", result: "...", metadata: {loop_index: 0}}
   - event: assistant, data: {content: "more text", metadata: {loop_index: 1, loop_reason: "tool_result_continuation"}}
   - event: lifecycle, data: {phase: "end", metadata: {loop_index: 1, loop_reason: "tool_result_continuation"}}
```

### 5.2 Channel Inbound Message Flow

```text
External Platform (for example Feishu)
1. ChannelHandler.handle_webhook() or message callback
   - Parse platform-specific payload -> InboundMessage
2. ChannelManager.receive_message()
   - Resolve user identity from channel context
   - Determine session key via DmScope routing
   - CommandParser.parse() - check for slash commands
3. AgentRunner.run() -> stream events
4. ChannelManager.send_message()
   - ChannelHandler.send() -> platform-specific API call
```

### 5.3 WebSocket Gateway Flow

```text
WebSocket Connection
1. ConnectFrame -> validate credentials
2. HelloOkFrame -> acknowledge connection
3. RequestFrame -> agent run (similar to REST flow)
4. EventFrame -> streaming events pushed to client
5. Disconnect -> cleanup connection state
```

---

## 6. Subsystem Interconnections

### Dependency Graph

```text
main (orchestrator)
|- core
|  |- config
|  |- deps
|  |- workspace
|  |- tenant
|  \- registry
|- auth
|  |- middleware
|  |- strategy
|  |- providers
|  \- user_store
|- channels
|  |- manager
|  |- registry
|  \- handlers
|- agent
|  |- runner
|  |- prompt
|  \- stream
|- api
|  |- routes
|  |- sse
|  \- gateway
|- tools
|  |- catalog
|  \- registration
|- skills
|  |- registry
|  \- loader
|- hooks
|  \- system
|- session
|- memory
\- messages
```

### Key Integration Points

| Producer | Consumer | Mechanism |
|---|---|---|
| `AuthMiddleware` | `api/routes.py` | `request.state.user` (UserInfo) |
| `api/routes.py` | `AgentRunner` | `SkillDeps` (request-scoped DI) |
| `AgentRunner` | `TokenPool` | `DynamicTokenPolicy.get_or_select_session_token()` |
| `AgentRunner` | `AgentInstancePool` | `get_or_create(agent_id, token)` |
| `AgentRunner` | `PromptBuilder` | `build(session, skills, tools)` |
| `AgentRunner` | `HookSystem` | `trigger(phase, context)` at each lifecycle point |
| `AgentRunner` | `SessionManager` | `load_transcript()`, `append_transcript()` |
| `AgentRunner` | `CompactionPipeline` | Triggered when context exceeds threshold |
| `TokenHealthInterceptor` | `TokenPool` | `update_token_health()` on LLM response |
| `TokenHealthInterceptor` | `TokenHealthStore` | `save()` after health update |
| `SkillRegistry` | PydanticAI Agent | `register_to_agent()` -wraps skills as agent tools |
| `ServiceProviderRegistry` | `SkillRegistry` | `register_skills_to()` -provider skills with instance awareness |
| `ChannelManager` | `AgentRunner` | `receive_message()` ->`run()` |
| `ConfigManager` | All modules | `get_config()` singleton access |

---

## 7. Configuration Architecture

### 7.1 Priority Chain (Highest to Lowest)

| Priority | Source | Example |
|---|---|---|
| 1 | Runtime overrides | `config_manager.set("agent_defaults.timeout_seconds", 600)` |
| 2 | Environment variables | `ATLASCLAW_AGENT_DEFAULTS__TIMEOUT_SECONDS=600` |
| 3 | User config | `~/.atlasclaw/config.json` |
| 4 | Workspace config | `.atlasclaw/atlasclaw.json` |
| 5 | Project config file | `atlasclaw.json` / `atlasclaw.yaml` in project root |
| 6 | Schema defaults | Defined in `config_schema.py` Pydantic models |

For reverse-proxied UI deployments, `AtlasClawConfig.base_path` defines the browser-visible mount path such as `/atlasclaw`. Frontend asset URLs, SPA routes, auth redirects, and cookie scope must all resolve through this configured base path.

### 7.2 Environment Variable Format

Pattern: `ATLASCLAW_<SECTION>__<KEY>` (double underscore for nesting)

Example: `ATLASCLAW_MODEL__PRIMARY=openai/gpt-4o`

### 7.3 Environment Variable Interpolation

Config values support `${VAR_NAME}` and `${VAR_NAME:default_value}` patterns:

```json
{
  "api_key": "${DEEPSEEK_API_KEY}",
  "base_url": "${DEEPSEEK_URL:https://api.deepseek.com}"
}
```

Resolved at config load time by `resolve_env()` in `models/providers.py`.

### 7.4 Key Configuration Sections

| Section | Schema Class | Controls |
|---|---|---|
| `model` | `ModelConfig` | LLM provider, primary token, fallbacks, temperature, selection strategy |
| `model.tokens` | `list[TokenConfig]` | Multi-token pool configuration |
| `model.selection_strategy` | `str` | Token selection strategy: `health`, `priority`, `random`, `round_robin` |
| `model.providers` | `ProviderConfig` | Legacy per-provider base_url, api_key, api_type |
| `service_providers` | (dict) | External service provider instances |
| `auth` | `AuthConfig` | Auth provider selection, OIDC settings, cache TTL |
| `agent_defaults` | `AgentDefaults` | Timeout, max tool calls, prompt mode, sandbox |
| `queue` | `QueueConfig` | Message queue mode, drop strategy |
| `reset` | `ResetConfig` | Session reset mode (daily/idle/manual), timing |
| `compaction` | `CompactionConfig` | Context compaction thresholds |
| `block_chunker` | `BlockChunkerConfig` | Streaming chunk size settings |
| `human_delay` | `HumanDelayConfig` | Response delay simulation |
| `webhook` | `WebhookConfig` | Webhook integration settings |

### 7.5 Per-User Configuration

Each user can have overrides at `<workspace>/users/<user_id>/atlasclaw.json`. Loaded via `ConfigManager.load_user_config(user_id)` and merged with the global config at request time.

---

## 8. Security Model

### 8.1 Authentication Pipeline

```text
Credential -> AuthProvider.authenticate() -> AuthResult
                                      |
                                      v
                       ensure_db_user_for_auth_result()
                                      |
                                      v
                               DB User -> UserInfo
```

**Credential Sources** (checked in order):
1. Configured host cookie auth header (`auth.host_cookie.header_name`)
2. `Authorization: Bearer <token>` header
3. Configured host cookie auth cookie (`auth.host_cookie.cookie_name`)

**Auth Providers** (configured in `auth.provider`):

*Primary Providers* (for user login, configured via `auth.provider`):
- `none` -No authentication; all requests get default user
- `local` -Username/password authentication against local user database

*Secondary Providers* (always available for specific use cases):
- `oidc_jwt` -JWT validation via JWKS for API authentication (bearer tokens)
- `oidc_login` -Full OAuth2 + PKCE authorization code flow for browser SSO

**Chained Authentication**: The system supports chained authentication where multiple providers can be tried in sequence. For example, local auth can be attempted first, followed by OIDC JWT validation if local fails.

**Data Encryption**: All sensitive data (API keys, tokens, credentials) is encrypted at rest using AES-256-GCM:
- Token API keys are encrypted in the database (`model_token_configs` table)
- Service provider configurations are encrypted
- Channel configurations are encrypted
- Config file values can use `enc:` prefix for automatic decryption on load

**Shadow User Store**: Maps external identities (`provider:subject`) to internal UUIDs. Idempotent under concurrent access. Stores `user_id`, `provider`, `subject`, `display_name`, `tenant_id`, `roles`, `created_at`, `last_seen_at`.

### 8.2 Authorization Enforcement

- **Workspace RBAC for management APIs**: AtlasClaw roles gate access to workspace administration APIs such as user, role, and permission governance endpoints
- **Upstream permission inheritance for runtime actions**: Skills and tools still execute under the authenticated user's upstream identity and token
- **Token propagation**: `SkillDeps.user_info.raw_token` is available to every skill for upstream API calls
- **Tool filtering**: `ToolPolicy` (allow/deny lists with wildcard support) restricts which tools are available per agent
- **Tenant isolation**: `TenantIsolation` provides path-based compartmentalization -session keys, memory paths, and storage paths are prefixed with `tenant_id`

### 8.3 Skip Paths (No Auth Required)

`/health`, `/ping`, `/favicon.ico`, `/docs`, `/openapi.json`, static assets (`/static/`, `/styles/`, `/scripts/`, `/locales/`), SSO endpoints (`/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`, `/api/auth/me`)

### 8.4 Multi-Tenant Isolation

```text
TenantIsolation.get_session_path(tenant_id)  -> <workspace>/tenants/<tenant_id>/sessions/
TenantIsolation.get_memory_path(tenant_id)   -> <workspace>/tenants/<tenant_id>/memory/
TenantIsolation.isolate_session_key(key, id) -> <tenant_id>:<session_key>
```

Tenant resolution order: explicit session key ->user header ->config default ->"default"

Quota enforcement: `TenantManager.check_quota()` validates `max_sessions`, `max_agents`, `max_memory_entries`, `max_concurrent_runs`.

---

## 9. Extension Points

AtlasClaw provides four main extension mechanisms:

### 9.1 Provider Extension

Providers are external integration packages loaded through `providers_root`. A
provider package contains:

```text
<providers_root>/<provider>/
|- PROVIDER.md          # Documentation + LLM context (frontmatter)
\- skills/
   |- skill-name/
   |  |- SKILL.md       # Skill documentation + frontmatter
   |  \- scripts/       # Executable handlers and helpers
   \- ...
```

**Discovery**: `ServiceProviderRegistry` loads provider templates and metadata
from `providers_root`. Startup then loads provider-owned skills from each
provider package's `skills/` directory.

**Instance Awareness**: A single provider type (e.g., "my-provider") can have
multiple instances (e.g., "production", "staging"). `ServiceProviderRegistry`
wraps skill handlers with automatic instance selection logic.

**Locations**:
1. Provider packages: `<providers_root>/<provider>/`
2. Provider-owned skills: `<providers_root>/<provider>/skills/`

### 9.2 Skill Extension

Skills are atomic, composable capabilities:

| Type | Description | Files |
|---|---|---|
| **Executable** | Python function with `SkillDeps` parameter | `handler.py` + `SKILL.md` |
| **Markdown** | LLM prompt/instruction in Markdown | `SKILL.md` only |
| **Hybrid** | Executable with Markdown documentation | Both files |

**Loading Sources**:
1. System-level capabilities owned by core
2. Standalone skills loaded from `skills_root`
3. Provider-owned skills loaded from `<providers_root>/<provider>/skills/`

**Registration**: `SkillRegistry.register()` for Python skills; `SkillRegistry.load_md_skills()` for Markdown skills. All skills are registered with the PydanticAI agent via `register_to_agent()`.

### 9.3 Channel Extension

Channel handlers are part of core and live under `app/atlasclaw/channels`.
Custom channel handlers implement `ChannelHandler` ABC:

- `setup(connection_config)` -Initialize with config
- `start(context)` / `stop()` -Lifecycle management
- `connect()` / `disconnect()` -For long-connection channels
- `validate_webhook()` / `handle_webhook()` -For webhook channels

Register via `ChannelRegistry.register(channel_type, handler_class)`.

### 9.4 Auth Provider Extension

Custom auth providers extend `AuthProvider` ABC:

- `provider_name` property -Unique identifier
- `authenticate(credential: str) -> AuthResult` -Validate credential

Register via `AuthRegistry.register(auth_id, provider_class)`.

### 9.5 Hook Extension

Register hooks for any of the 17+ lifecycle phases:

```python
HookDefinition(
    phase=HookPhase.BEFORE_TOOL_CALL,
    handler=my_async_handler,
    priority=100,
    mode=HookExecutionMode.SEQUENTIAL,
)
```

Hook handler signature: `async (context: dict) -> Optional[dict]`

### 9.6 Hook Runtime Extension

AtlasClaw also provides a runtime-first hook layer aligned with OpenClaw-style
event capture. This layer is generic by design: it dispatches lifecycle events,
stores per-user module state, and exposes memory/context sinks without baking
skill-specific semantics into the core.

**Core components**:
- `HookRuntime` -dispatches typed runtime events to registered handlers
- `HookStateStore` -persists per-user, per-module hook state
- `MemorySink` -writes confirmed items into long-term memory files
- `ContextSink` -exposes confirmed items for future prompt/context integration

**Runtime event taxonomy**:
- `run.started`, `run.completed`, `run.failed`
- `run.context_ready`
- `message.received`, `message.user_corrected`
- `llm.requested`, `llm.completed`, `llm.failed`
- `tool.started`, `tool.completed`, `tool.failed`
- `memory.confirmed`, `memory.rejected`

**Phase 1 registration model**:
- Hook runtime handlers are registered in-process during bootstrap
- Core remains generic: no skill-specific event types or module semantics are baked in
- Skills or providers may consume the runtime later, but they are not part of the core contract

**Script hook support**:
- Config-driven local executable commands may subscribe through `hooks_runtime.script_handlers`
- Commands receive event envelopes over `stdin`
- Commands may emit structured `stdout` JSON actions:
  - `create_pending`
  - `write_memory`
  - `add_context`

---

## 10. Streaming & Real-Time Communication

### 10.1 StreamEvent Pipeline

The agent engine produces `StreamEvent` objects through an async iterator:

| Event Type | Phases/Fields | Purpose |
|---|---|---|
| `LIFECYCLE` | start, end, error, aborted | Run lifecycle markers and model-loop boundaries |
| `ASSISTANT` | content (delta text) | Incremental LLM output; loop metadata is carried in `metadata` |
| `TOOL` | start, update, end + tool name | Tool execution tracking and capability continuation |
| `COMPACTION` | start, end | Context compaction markers |
| `ERROR` | error message | Error reporting |

Every model re-entry should emit explicit loop metadata in `metadata`, including:

- `loop_index`
- `loop_reason` (`initial_request`, `tool_result_continuation`, `invalid_markup_recovery`, `artifact_completion_recovery`)
- `selected_capability_ids`

### 10.2 SSE Transport

- **Format**: Standard Server-Sent Events with `event:` and `data:` fields
- **Event IDs**: Each event has a monotonic ID for `Last-Event-ID` resume support
- **Heartbeat**: Periodic `:heartbeat` comments to keep connection alive
- **Retry**: Configurable reconnect interval via `retry:` field

### 10.3 WebSocket Transport

- Uses the Gateway frame protocol (`ConnectFrame`, `HelloOkFrame`, `RequestFrame`, `ResponseFrame`, `EventFrame`)
- Connection state machine: `CONNECTING ->CONNECTED ->AUTHENTICATED ->DISCONNECTING ->DISCONNECTED`
- Multiplexed: single connection supports multiple concurrent request/response pairs

### 10.4 Block Chunker

Configurable via `BlockChunkerConfig`, controls how streaming text is chunked for delivery to channels that require complete blocks (e.g., IM platforms).

### 10.5 Unified Heartbeat Runtime

AtlasClaw includes a unified `HeartbeatRuntime` for periodic health and maintenance work.
It is intentionally separate from transport-level keepalive in SSE/WebSocket and separate from
the Hook Runtime's downstream event-consumption responsibilities.

Core responsibilities:

- schedule and execute typed heartbeat jobs
- persist heartbeat job definitions, state snapshots, and local event logs
- emit standard heartbeat runtime events
- bridge heartbeat outcomes into Hook Runtime consumers
- support both OpenClaw-style agent heartbeat turns and AtlasClaw channel connection supervision

Supported job types:

- `agent_turn`
  - periodic autonomous agent turn
  - supports `HEARTBEAT.md`, `every`, `activeHours`, `isolatedSession`, and `HEARTBEAT_OK`
- `channel_connection`
  - periodic long-connection health probe and reconnect supervision
  - used for Feishu, DingTalk, WeCom, and other long-lived channel connections

Design rules:

- Heartbeat emits runtime events first; notification is handled by downstream consumers.
- Group-chat targets default to summary-only delivery semantics.
- Channel heartbeat retries quietly before surfacing degraded or failure events.
- Agent heartbeat may use isolated sessions to avoid polluting primary user conversation history.

---


### 10.6 Tool Necessity Gate Runtime

AtlasClaw includes a Phase 1 runtime policy layer between prompt construction and unrestricted
model completion. Its purpose is to stop unsupported final answers when the request actually
requires live data, external systems, browser interaction, or grounded verification.

The runtime policy has three coordinated parts:

- `ToolNecessityGate` - classifies whether direct answering is allowed after the prompt and capability surface are prepared
- `CapabilityMatcher` - maps required capability classes to currently visible runtime capabilities
- `Mandatory Tool Enforcement` - blocks the final answer if the runtime requires tools and no grounded tool path succeeds

Phase 1 is classifier-first, not keyword-first:

- the runtime accepts an explicit classifier decision when available
- model-backed classification is an opt-in backend and is disabled by default
- invalid classifier output is discarded
- if no classifier is available, the gate falls back to a neutral direct-answer default
- hard-coded business keyword tables are intentionally not the platform contract

The runtime emits tool-gate and enforcement events into Hook Runtime so downstream consumers can inspect why a turn was allowed to answer directly, required tools, or was blocked for missing grounding. The gate does not own the main turn-intent decision and does not introduce a separate planner call on the hot path.
## 11. Data Storage Model

### 11.1 Sessions

**Format**: JSONL (one JSON object per line)
**Location**: `<workspace>/users/<user_id>/sessions/`
**Files**:
- `<session_id>.jsonl` -Main transcript
- `<session_id>-topic-<thread_id>.jsonl` -Thread transcripts
- `sessions.json` -Session metadata index

**TranscriptEntry**:
```json
{"role": "user", "content": "...", "timestamp": "...", "metadata": {...}}
{"role": "assistant", "content": "...", "timestamp": "...", "metadata": {...}}
{"role": "tool", "content": "...", "timestamp": "...", "metadata": {"tool": "web_search"}}
```

**Concurrency**: Per-session `asyncio.Lock` prevents concurrent writes.

### 11.2 Memory

**Format**: Markdown files
**Location**: `<workspace>/users/<user_id>/memory/`
**Files**:
- `YYYY-MM-DD.md` -Daily memories (date-scoped)
- `MEMORY.md` -Long-term persistent memory

**Search**: Hybrid search combining keyword matching and semantic similarity.

Confirmed runtime output may also be promoted into timestamped long-term memory
documents such as `memory_<timestamp>.md` under the same per-user memory
directory.

### 11.3 Hook Runtime State

**Format**: JSONL
**Location**: `<workspace>/users/<user_id>/hooks/<module_name>/`
**Files**:
- `events.jsonl` -Observed runtime lifecycle events
- `pending.jsonl` -Items awaiting explicit confirmation or rejection
- `decisions.jsonl` -Confirmation/rejection history

**Isolation**:
- State is scoped by authenticated AtlasClaw `user_id`
- Hook modules do not share files unless they intentionally use the same module name

### 11.4 Heartbeat Runtime State

**Format**: JSON / JSONL
**Location**: `<workspace>/users/<user_id>/heartbeat/`
**Files**:
- `jobs.json` -Registered heartbeat job definitions
- `state.json` -Durable heartbeat job state snapshots
- `events.jsonl` -Local heartbeat runtime event log

Heartbeat state is intentionally separate from Hook Runtime state:

- heartbeat owns scheduling, retry, health, and delivery target resolution
- hooks consume emitted heartbeat events downstream

### 11.4 Users

**Format**: Relational database row
**Location**: `users` table
**Content**: Local and external user identities, role assignments, active state, and profile fields

### 11.5 Configuration

**Format**: JSON or YAML
**Locations**: 
- Project root (`atlasclaw.json`) -Core configuration
- Workspace root (`<workspace>/atlasclaw.json`) -Workspace configuration
- User home (`~/.atlasclaw/config.json`) -Global user configuration
- Per-user (`<workspace>/users/<user_id>/user_setting.json`) -User-specific settings (channels, preferences)

### 11.6 Token Health

**Format**: JSON
**Location**: `<workspace>/token_health.json`
**Content**: Token health snapshots for rate-limit tracking

```json
{
  "token-1": {
    "remaining_tokens": 95000,
    "remaining_requests": 95,
    "reset_tokens_seconds": 60,
    "reset_requests_seconds": 30,
    "updated_at": "2026-03-17T10:30:00+00:00"
  }
}
```

### 11.7 Relational Database (Enterprise)

**Tables** (SQLAlchemy ORM models in `app/atlasclaw/db/models.py`):

| Table | Purpose | Key Fields |
|---|---|---|
| `agents` | Agent configuration storage | `id`, `name` (unique), `display_name`, `identity` (JSON), `soul` (JSON), `memory` (JSON) |
| `model_token_configs` | LLM token/API key storage (encrypted) | `id`, `name` (unique), `provider`, `model`, `api_key` (AES-256-GCM encrypted), `priority`, `weight` |
| `users` | User accounts | `id`, `username` (unique), `password_hash`, `roles` (JSON) |
| `channels` | Channel configurations (encrypted) | `id`, `name`, `type`, `user_id`, `config` (AES-256-GCM encrypted JSON), `is_default` |
| `service_provider_configs` | Provider instance configs (encrypted) | `id`, `provider_type`, `instance_name`, `config` (AES-256-GCM encrypted JSON), `is_active` |

**Unique Constraints**:
- `agents.name`
- `model_token_configs.name`
- `users.username`
- `service_provider_configs.(provider_type, instance_name)`

**API Endpoints** (`/api/*`):
- `GET/POST /agent-configs` -Agent CRUD
- `GET/POST /token-configs` -Token CRUD
- `GET/POST /users` -User CRUD
- `GET/POST /provider-configs` -Provider instance CRUD

**Merge Behavior**:
- Tokens: DB entries override JSON entries with same `token_id`
- Provider instances: DB entries override JSON entries with same `(provider_type, instance_name)`

### 11.8 Data Encryption

All sensitive data is encrypted at rest using **AES-256-GCM**:

**Encryption Service** (`app/atlasclaw/core/encryption.py`):
- Algorithm: AES-256-GCM with authenticated encryption
- Key: 32-byte key (configurable via `ATLASCLAW_ENCRYPTION_KEY` environment variable)
- Format: `v1:key_id:base64(nonce+ciphertext+tag)`

**Encrypted Fields**:
- `TokenModel.api_key` -LLM API keys in database
- `ServiceProviderConfigModel.config` -Provider credentials and settings
- `ChannelModel.config` -Channel connection credentials

**Config File Encryption**:
- Values with `enc:` prefix are automatically decrypted on load
- Example: `"api_key": "enc:v1:default:AbC..."`

**Transparent Encryption**:
- API accepts plaintext values
- Database stores encrypted values
- API returns plaintext values (decrypted automatically)
- Lazy migration: legacy unencrypted data is read as-is

### 11.9 Database Initialization

**SQLite Mode** (default):
- Schema auto-created via `Base.metadata.create_all()` on startup
- No manual migrations required
- Suitable for development and single-node deployments

**MySQL Mode** (enterprise):
- Requires Alembic migrations (`alembic upgrade head`)
- Migrations located in `migrations/versions/`
- Suitable for multi-node and production deployments

---

## 12. Technology Stack

### Backend

| Component | Technology | Purpose |
|---|---|---|
| Web framework | FastAPI + Uvicorn | ASGI HTTP/WebSocket server |
| Agent framework | PydanticAI | LLM orchestration, tool calling, streaming |
| Data validation | Pydantic v2 | Config models, request/response schemas |
| Auth (JWT) | PyJWT + cryptography | OIDC token validation |
| Auth (OAuth2) | httpx | OAuth2 authorization code flow |
| HTTP client | httpx | Async HTTP requests |
| Testing | pytest + pytest-asyncio | Test framework with async support |
| Process management | multiprocessing | Feishu SDK isolation |
| ORM | SQLAlchemy 2.0 + async | Database models and queries |
| Migrations | Alembic | MySQL DDL/DML version control |

### Frontend

| Component | Technology | Purpose |
|---|---|---|
| Build tool | esbuild | ES module bundling |
| UI framework | Vanilla JS + Deep Chat | Chat interface |
| Testing | Jest + jsdom | Frontend unit tests |
| i18n | Custom i18n.js | Chinese/English localization |

### Runtime Requirements

- **Python**: 3.10+ (3.11+ recommended)
- **Node.js**: For frontend build only
- **Database**: SQLite (default, zero-config) or MySQL (enterprise, via Alembic)
- **No Redis**: Optional for caching (not required)

---

> **Cross-Reference Index**
>
> | Topic | Document | Section |
> |---|---|---|
> | Per-module API details | [Module Details](./MODULE-DETAILS.MD) | All sections |
> | Code style rules | [Development Spec](./DEVELOPMENT-SPEC.MD) | Section 1 Code Style |
> | Testing requirements | [Development Spec](./DEVELOPMENT-SPEC.MD) | Section 6 Testing |
> | Error handling patterns | [Development Spec](./DEVELOPMENT-SPEC.MD) | Section 3 Error Handling |
> | Provider creation guide | [Provider Guide](./PROVIDER-GUIDE.MD) | Full document |
> | Skill creation guide | [Skill Guide](./SKILL-GUIDE.MD) | Full document |
> | Channel integration | [Channel Guide](./CHANNEL-GUIDE.MD) | Full document |
> | Deployment & operations | [Deployment](./DEPLOYMENT.MD) | Full document |
> | User/developer guide | [Overview](./OVERVIEW.MD) | Full document |
