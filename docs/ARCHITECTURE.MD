# AtlasClaw Architecture

> **Canonical architecture reference for the AtlasClaw enterprise agent framework.**
> All future development and feature changes MUST consult this document.
>
> Related documents:
> - [Module Details](./MODULE-DETAILS.MD) — per-module API surface and internals
> - [Development Specification](./DEVELOPMENT-SPEC.MD) — code standards, patterns, and review checklist

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

The framework core handles routing, lifecycle, and coordination. All domain-specific logic lives in **providers** — modular packages that encapsulate authentication, skills, and configuration for external systems (ITSM, CRM, monitoring, communication platforms). The core never contains business logic for specific integrations.

### 1.2 Permission Inheritance (No RBAC Bypass)

AtlasClaw never creates its own authorization layer. Every operation inherits the authenticated user's permissions from the connected enterprise system. If a user cannot perform an action in the upstream system, the agent cannot perform it on their behalf. This is enforced through `SkillDeps.user_info` propagation to every skill and tool invocation.

### 1.3 Async-First

All I/O-bound operations are async. The framework uses `asyncio.Event` for cancellation/coordination signals, async iterators for streaming, and `asyncio.Lock` for per-session concurrency control. Blocking operations are not permitted in the request path.

### 1.4 File-System Storage

State persistence uses the local file system with structured formats:
- **Sessions**: JSONL (one JSON object per line, append-only)
- **Memory**: Markdown files (daily date-scoped + long-term)
- **Configuration**: JSON/YAML files with environment variable interpolation
- **Users**: JSON (shadow user store)

No external database is required. This simplifies deployment and supports embedded-mode operation.

### 1.5 Multi-Channel Access

A single agent instance serves multiple access channels simultaneously (REST API, WebSocket, SSE, Feishu, DingTalk, WeCom). Channel handlers normalize inbound messages into a common `InboundMessage` format and translate outbound responses back to channel-specific formats. The agent engine is channel-agnostic.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Access Channels                          │
│  ┌─────────┐ ┌──────────┐ ┌─────┐ ┌──────┐ ┌────────┐ ┌─────┐│
│  │WebSocket│ │   SSE    │ │REST │ │Feishu│ │DingTalk│ │WeCom││
│  └────┬────┘ └────┬─────┘ └──┬──┘ └──┬───┘ └───┬────┘ └──┬──┘│
│       └───────────┬┴─────────┴───────┴─────────┴──────────┘   │
│                   ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                     API Layer                            │   │
│  │   routes.py · gateway.py · sse.py · webhook_dispatch.py  │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  Auth Middleware                           │  │
│  │  credential extraction → provider.authenticate()          │  │
│  │  → shadow_store.get_or_create() → UserInfo                │  │
│  └──────────────────────┬───────────────────────────────────┘  │
│                         ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   Agent Engine                            │  │
│  │  AgentRunner · PromptBuilder · CompactionPipeline         │  │
│  │  PydanticAI Agent · StreamEvent pipeline                  │  │
│  │  TokenPool · DynamicTokenPolicy · AgentInstancePool       │  │
│  │  TokenHealthStore · TokenHealthInterceptor                │  │
│  └──────┬──────────────┬──────────────────┬─────────────────┘  │
│         ▼              ▼                  ▼                     │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐            │
│  │  Tools   │  │   Skills     │  │   Hooks       │            │
│  │ 16+ built│  │ Executable + │  │ 17+ lifecycle │            │
│  │ -in tools│  │ Markdown +   │  │ phases        │            │
│  │ profiles │  │ Provider     │  │ seq/parallel  │            │
│  └──────────┘  └──────────────┘  └───────────────┘            │
│         │              │                  │                     │
│         ▼              ▼                  ▼                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                 Core Infrastructure                       │  │
│  │  ConfigManager · SkillDeps · WorkspaceInitializer         │  │
│  │  TenantManager · ServiceProviderRegistry                  │  │
│  └──────────────────────┬───────────────────────────────────┘  │
│                         ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                Data & State Management                    │  │
│  │  SessionManager (JSONL) · MemoryManager (Markdown)        │  │
│  │  ShadowUserStore (JSON) · ChannelStore (JSON)             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │               External LLM Providers                      │  │
│  │  DeepSeek · OpenAI · Doubao · Kimi · Anthropic (via API) │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Responsibility | Key Modules |
|---|---|---|
| **Access Channels** | Protocol adaptation, message normalization | `channels/handlers/*` |
| **API Layer** | HTTP routing, SSE streaming, WebSocket frames, webhook dispatch | `api/routes.py`, `api/sse.py`, `api/gateway.py` |
| **Auth Middleware** | Credential extraction, provider authentication, shadow user resolution | `auth/middleware.py`, `auth/strategy.py` |
| **Agent Engine** | LLM orchestration, prompt construction, streaming, context compaction, token management, agent instance pooling | `agent/runner.py`, `agent/prompt_builder.py`, `agent/token_policy.py`, `agent/agent_pool.py`, `core/token_pool.py`, `core/token_health_store.py`, `core/token_interceptor.py` |
| **Tools & Skills** | Executable capabilities exposed to the LLM | `tools/`, `skills/` |
| **Hooks** | Lifecycle interception for cross-cutting concerns | `hooks/system.py` |
| **Core Infrastructure** | Configuration, dependency injection, workspace, tenancy | `core/` |
| **Data & State** | Persistence for sessions, memory, users, channels, token health | `session/`, `memory/`, `auth/shadow_store.py` |

---

## 3. Directory Structure

```
app/atlasclaw/
├── main.py                        # FastAPI application factory + lifespan
├── core/
│   ├── config.py                  # ConfigManager (6-level priority chain)
│   ├── config_schema.py           # Pydantic config models + enums
│   ├── deps.py                    # SkillDeps dataclass (request-scoped DI)
│   ├── workspace.py               # WorkspaceInitializer, UserWorkspaceInitializer
│   ├── provider_registry.py       # ServiceProviderRegistry
│   ├── provider_scanner.py        # ProviderScanner (filesystem discovery)
│   ├── tenant.py                  # TenantManager, TenantConfig, TenantIsolation
│   ├── token_pool.py              # TokenPool, TokenEntry, TokenHealth
│   ├── token_health_store.py      # TokenHealthStore (persistence)
│   └── token_interceptor.py       # TokenHealthInterceptor (rate-limit headers)
├── auth/
│   ├── models.py                  # UserInfo, AuthResult, ShadowUser
│   ├── middleware.py              # AuthMiddleware (BaseHTTPMiddleware)
│   ├── strategy.py                # AuthStrategy (cache + resolve pipeline)
│   ├── registry.py                # AuthRegistry (pluggable providers)
│   ├── shadow_store.py            # ShadowUserStore (JSON persistence)
│   ├── config.py                  # AuthConfig, OIDCConfig
│   └── providers/
│       ├── base.py                # AuthProvider ABC
│       ├── none.py                # NoneProvider (no auth)
│       ├── api_key.py             # APIKeyProvider
│       ├── oidc.py                # OIDCProvider (JWT/JWKS validation)
│       ├── oidc_sso.py            # OIDCSSOProvider (full OAuth2 + PKCE)
│       └── smartcmp.py            # SmartCMPProvider (CloudChef)
├── agent/
│   ├── runner.py                  # AgentRunner (main execution loop)
│   ├── stream.py                  # StreamEvent, StreamEventType
│   ├── prompt_builder.py          # PromptBuilder (FULL/MINIMAL/NONE modes)
│   ├── routing.py                 # AgentConfig, DmScope, ToolPolicy
│   ├── agent_definition.py        # AgentLoader, AgentDefinitionParser
│   ├── compaction.py              # CompactionPipeline (context compression)
│   ├── token_policy.py            # DynamicTokenPolicy (session-level token selection)
│   └── agent_pool.py              # AgentInstancePool (agent instance caching)
├── api/
│   ├── routes.py                  # REST endpoints, APIContext
│   ├── gateway.py                 # Gateway frames, ConnectionInfo
│   ├── sse.py                     # SSEManager, SSEEvent
│   ├── channels.py                # Channel management endpoints
│   ├── agent_info.py              # Agent info endpoints
│   ├── webhook_dispatch.py        # Webhook routing
│   ├── channel_hooks.py           # Channel-specific hooks
│   ├── response_handler.py        # Response normalization
│   └── request_orchestrator.py    # Request routing logic
├── channels/
│   ├── base.py                    # ChannelConfig, ChannelMessage
│   ├── handler.py                 # ChannelHandler ABC
│   ├── models.py                  # ChannelMode, InboundMessage, OutboundMessage, SendResult
│   ├── manager.py                 # ChannelManager
│   ├── registry.py                # ChannelRegistry
│   ├── store.py                   # ChannelStore (persistence)
│   └── handlers/
│       ├── websocket.py           # WebSocketHandler
│       ├── rest.py                # RESTHandler
│       ├── sse.py                 # SSEHandler
│       ├── feishu.py              # FeishuHandler (multiprocessing isolation)
│       ├── dingtalk.py            # DingTalkHandler (stream mode)
│       └── wecom.py               # WeComHandler
├── tools/
│   ├── catalog.py                 # ToolProfile, ToolCatalog, tool groups
│   ├── registration.py            # register_builtin_tools()
│   ├── base.py                    # Tool base classes
│   ├── filesystem/                # read, write, edit, delete_file
│   ├── runtime/                   # exec, process
│   ├── memory/                    # memory_search, memory_get
│   ├── sessions/                  # list, history, send, spawn, status, subagents
│   ├── web/                       # web_search, web_fetch
│   ├── ui/                        # browser
│   └── providers/                 # instance_tools (list/select provider instances)
├── skills/
│   ├── registry.py                # SkillRegistry, SkillMetadata, MdSkillEntry
│   ├── frontmatter.py             # parse_frontmatter(), FrontmatterResult
│   └── loader.py                  # SkillLoader
├── session/
│   ├── context.py                 # SessionKey, SessionScope, SessionMetadata, TranscriptEntry
│   ├── manager.py                 # SessionManager (JSONL persistence)
│   └── queue.py                   # SessionQueue, QueueMode
├── memory/
│   ├── manager.py                 # MemoryManager, MemoryEntry, MemoryType
│   └── hybrid_search.py           # Hybrid search (keyword + semantic)
├── models/
│   ├── providers.py               # ProviderConfig, ModelDefinition
│   └── failover.py                # AuthProfile, ModelFailoverConfig
├── hooks/
│   └── system.py                  # HookSystem, HookPhase, HookDefinition
├── workflow/
│   ├── engine.py                  # WorkflowEngine, WorkflowStep, StepStatus
│   └── orchestrator.py            # Orchestration logic
├── messages/
│   ├── handler.py                 # InboundMessage, OutboundMessage, MessageType
│   └── command.py                 # CommandParser, ParsedCommand, CommandDefinition
├── media/
│   ├── link_extractor.py          # URL extraction from text
│   ├── tts.py                     # Text-to-speech
│   └── understanding.py           # Media content understanding
└── providers/                     # Built-in provider integrations
    └── <provider>/
        ├── PROVIDER.md            # Provider documentation + LLM context
        └── skills/                # Provider-specific skills

app/frontend/
├── index.html                     # Main chat interface
├── channels.html                  # Channel management UI
├── package.json                   # esbuild-based build
├── scripts/
│   ├── main.js                    # Application entry point
│   ├── api-client.js              # HTTP client wrapper
│   ├── channel-manager.js         # Channel connection management
│   ├── chat-ui.js                 # Chat UI components
│   ├── config.js                  # Frontend configuration
│   ├── i18n.js                    # Internationalization (zh-CN, en-US)
│   ├── session-manager.js         # Session lifecycle
│   └── stream-handler.js          # SSE stream handling
├── styles/                        # CSS stylesheets
├── static/                        # Static assets
└── locales/                       # i18n translation files

tests/
├── atlasclaw/                     # Python test suite (~48 test files)
│   ├── conftest.py                # Shared fixtures
│   ├── test_agent.py              # Agent engine tests
│   ├── test_config.py             # Configuration tests
│   ├── test_session.py            # Session management tests
│   └── ...
└── frontend/                      # JavaScript tests (~6 test files)

providers/                         # Provider repository (code/templates)
└── <provider_name>/
    ├── PROVIDER.md
    ├── auth/
    ├── channels/
    └── skills/

<workspace>/                       # Runtime workspace (default: ./.atlasclaw)
├── agents/
│   └── main/
│       ├── SOUL.md                # Agent capabilities and behavior
│       ├── IDENTITY.md            # Agent personality
│       ├── USER.md                # User interaction patterns
│       └── MEMORY.md              # Memory strategy
└── users/
    └── <user_id>/
        ├── user_setting.json      # User-specific config (channels, preferences)
        ├── sessions/              # JSONL session transcripts
        └── memory/                # Markdown memory files
```

---

## 4. Application Bootstrap & Lifespan

The application starts via `app/atlasclaw/main.py`:

```
create_app() → FastAPI(lifespan=lifespan)
```

### Initialization Sequence (Startup)

The `lifespan()` async context manager executes this ordered sequence:

1. **Load configuration** — `ConfigManager.load()` reads `atlasclaw.json`/`atlasclaw.yaml` with environment variable interpolation
2. **Initialize workspace** — `WorkspaceInitializer.initialize()` creates the `.atlasclaw/` directory tree (user data only)
3. **Register built-in channel handlers** — Feishu, DingTalk, WeCom handlers registered in `ChannelRegistry`
4. **Initialize ChannelManager** — Creates `ChannelManager` with workspace path
5. **Scan providers** — `ProviderScanner` walks the providers directory (outside workspace), discovers auth and channel extensions
6. **Load agent definitions** — `AgentLoader` reads `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md` from `agents/main/`
7. **Initialize TokenPool** — Creates `TokenPool` from `model.tokens` config, restores health state from `TokenHealthStore`
8. **Initialize SessionManager** — Creates session manager with configured reset mode
9. **Load ServiceProviderRegistry** — Reads service provider configurations, resolves environment variables
10. **Register built-in tools** — `register_builtin_tools()` with the configured `ToolProfile`
11. **Load skills** — From 4 sources in priority order: workspace → workspace providers → global → built-in
12. **Create AgentInstancePool** — Creates pool for agent instance caching with concurrency control
13. **Create PydanticAI Agent** — Instantiates the agent with resolved model configuration
14. **Register skills to agent** — `SkillRegistry.register_to_agent()`
15. **Create AgentRunner** — With agent, session manager, prompt builder, compaction pipeline, hook system, session queue, token policy, agent pool, token interceptor
16. **Set up APIContext** — Global context holder for all API routes
17. **Start channel connections** — Initialize active channel connections

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

```
HTTP Request (POST /api/agent/run)
│
├─ 1. AuthMiddleware.dispatch()
│     ├─ Extract credential (header → cookie → bearer)
│     ├─ AuthStrategy.resolve_user(credential)
│     │     ├─ Check TTL cache (300s default)
│     │     ├─ AuthProvider.authenticate(credential) → AuthResult
│     │     └─ ShadowUserStore.get_or_create() → ShadowUser → UserInfo
│     └─ Attach UserInfo to request.state.user
│
├─ 2. Route handler (api/routes.py)
│     ├─ Parse AgentRunRequest (session_key, message, model, timeout)
│     ├─ Resolve session key via SessionScope
│     └─ Build SkillDeps (user_info, session, memory managers, abort_signal)
│
├─ 3. AgentRunner.run() → AsyncIterator[StreamEvent]
│     ├─ Emit lifecycle_start
│     ├─ Select/pin token via DynamicTokenPolicy.get_or_select_session_token()
│     ├─ Get or create agent instance from AgentInstancePool
│     ├─ Load session transcript (JSONL)
│     ├─ Build message history from transcript
│     ├─ PromptBuilder.build() → system prompt
│     ├─ Trigger BEFORE_PROMPT_BUILD hook
│     ├─ CompactionPipeline (if context exceeds threshold)
│     ├─ Check SessionQueue for steering messages
│     ├─ PydanticAI Agent.run_stream() → model inference
│     │     ├─ Yield assistant deltas (StreamEvent.ASSISTANT)
│     │     ├─ Execute tool calls (StreamEvent.TOOL start/end)
│     │     ├─ TokenHealthInterceptor.on_response() → update rate limits
│     │     └─ Enforce max_tool_calls and timeout_seconds
│     ├─ Persist transcript entries (append to JSONL)
│     └─ Emit lifecycle_end
│
└─ 4. SSE Response
      ├─ SSEManager.create_stream() → event: lifecycle, data: {phase: "start"}
      ├─ event: assistant, data: {content: "delta text"}
      ├─ event: tool, data: {phase: "start", tool: "web_search"}
      ├─ event: tool, data: {phase: "end", tool: "web_search", result: "..."}
      ├─ event: assistant, data: {content: "more text"}
      └─ event: lifecycle, data: {phase: "end"}
```

### 5.2 Channel Inbound Message Flow

```
External Platform (e.g., Feishu)
│
├─ 1. ChannelHandler.handle_webhook() or message callback
│     └─ Parse platform-specific payload → InboundMessage
│
├─ 2. ChannelManager.receive_message()
│     ├─ Resolve user identity from channel context
│     ├─ Determine session key via DmScope routing
│     └─ CommandParser.parse() — check for /commands
│
├─ 3. AgentRunner.run() → stream events
│
└─ 4. ChannelManager.send_message()
      └─ ChannelHandler.send() → platform-specific API call
```

### 5.3 WebSocket Gateway Flow

```
WebSocket Connection
│
├─ 1. ConnectFrame → validate credentials
├─ 2. HelloOkFrame → acknowledge connection
├─ 3. RequestFrame → agent run (similar to REST flow)
├─ 4. EventFrame ← streaming events pushed to client
└─ 5. Disconnect → cleanup connection state
```

---

## 6. Subsystem Interconnections

### Dependency Graph

```
                    ┌─────────┐
                    │  main   │ (orchestrator)
                    └────┬────┘
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    ┌──────────┐  ┌──────────┐  ┌──────────────┐
    │   core   │  │   auth   │  │   channels   │
    │ config   │  │ middleware│  │   manager    │
    │ deps     │  │ strategy │  │   registry   │
    │ workspace│  │ providers│  │   handlers   │
    │ tenant   │  │ shadow   │  └──────┬───────┘
    │ registry │  └────┬─────┘         │
    └────┬─────┘       │               │
         │             ▼               ▼
         │      ┌──────────┐    ┌──────────┐
         ├─────►│  agent   │◄───│   api    │
         │      │ runner   │    │ routes   │
         │      │ prompt   │    │ sse      │
         │      │ stream   │    │ gateway  │
         │      └────┬─────┘    └──────────┘
         │           │
         │     ┌─────┼─────┐
         │     ▼     ▼     ▼
         │ ┌──────┐┌─────┐┌──────┐
         │ │tools ││skill││hooks │
         │ │catlog││regis││system│
         │ └──┬───┘└──┬──┘└─────┘
         │    │       │
         │    ▼       ▼
         │ ┌────────────────┐
         └►│  session       │
           │  memory        │
           │  messages      │
           └────────────────┘
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
| `SkillRegistry` | PydanticAI Agent | `register_to_agent()` — wraps skills as agent tools |
| `ServiceProviderRegistry` | `SkillRegistry` | `register_skills_to()` — provider skills with instance awareness |
| `ChannelManager` | `AgentRunner` | `receive_message()` → `run()` |
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

```
Credential → AuthProvider.authenticate() → AuthResult
                                              │
                                              ▼
                               ShadowUserStore.get_or_create()
                                              │
                                              ▼
                                     ShadowUser → UserInfo
```

**Credential Sources** (checked in order):
1. `CloudChef-Authenticate` header
2. `Authorization: Bearer <token>` header
3. `CloudChef-Authenticate` cookie

**Auth Providers** (mutually exclusive, configured in `auth.provider`):
- `none` — No authentication; all requests get default user
- `api_key` — Static API key validation
- `oidc` — JWT validation via JWKS (no SSO flow)
- `oidc_sso` — Full OAuth2 + PKCE authorization code flow
- `smartcmp` — CloudChef SmartCMP token validation

**Shadow User Store**: Maps external identities (`provider:subject`) to internal UUIDs. Idempotent under concurrent access. Stores `user_id`, `provider`, `subject`, `display_name`, `tenant_id`, `roles`, `created_at`, `last_seen_at`.

### 8.2 Authorization Enforcement

- **No internal RBAC**: The agent inherits the user's permissions from the upstream system
- **Token propagation**: `SkillDeps.user_info.raw_token` is available to every skill for upstream API calls
- **Tool filtering**: `ToolPolicy` (allow/deny lists with wildcard support) restricts which tools are available per agent
- **Tenant isolation**: `TenantIsolation` provides path-based compartmentalization — session keys, memory paths, and storage paths are prefixed with `tenant_id`

### 8.3 Skip Paths (No Auth Required)

`/health`, `/ping`, `/favicon.ico`, `/docs`, `/openapi.json`, static assets (`/static/`, `/styles/`, `/scripts/`, `/locales/`), SSO endpoints (`/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`, `/api/auth/me`)

### 8.4 Multi-Tenant Isolation

```
TenantIsolation.get_session_path(tenant_id)  → <workspace>/tenants/<tenant_id>/sessions/
TenantIsolation.get_memory_path(tenant_id)   → <workspace>/tenants/<tenant_id>/memory/
TenantIsolation.isolate_session_key(key, id) → <tenant_id>:<session_key>
```

Tenant resolution order: explicit session key → user header → config default → "default"

Quota enforcement: `TenantManager.check_quota()` validates `max_sessions`, `max_agents`, `max_memory_entries`, `max_concurrent_runs`.

---

## 9. Extension Points

AtlasClaw provides four main extension mechanisms:

### 9.1 Provider Extension

Providers are the primary extension point. A provider is a directory containing:

```
<provider>/
├── PROVIDER.md          # Documentation + LLM context (frontmatter)
└── skills/
    ├── skill-name/
    │   ├── SKILL.md     # Skill documentation + frontmatter
    │   └── handler.py   # Executable skill (optional)
    └── ...
```

**Discovery**: `ProviderScanner` walks the providers directory at startup. Provider directories can also contribute auth providers and channel handlers.

**Instance Awareness**: A single provider type (e.g., "smartcmp") can have multiple instances (e.g., "production", "staging"). `ServiceProviderRegistry` wraps skill handlers with automatic instance selection logic.

**Locations** (override priority):
1. Workspace: `<workspace>/skills/` (optional, for development)
2. Global: `~/.atlasclaw/skills/`
3. Skills root: `<skills_root>/` (configured in atlasclaw.json, default: `../atlasclaw-providers/skills`)
4. Providers: `<providers_root>/<provider>/skills/` (configured in atlasclaw.json, default: `../atlasclaw-providers/providers`)
5. Built-in: `app/atlasclaw/providers/`

### 9.2 Skill Extension

Skills are atomic, composable capabilities:

| Type | Description | Files |
|---|---|---|
| **Executable** | Python function with `SkillDeps` parameter | `handler.py` + `SKILL.md` |
| **Markdown** | LLM prompt/instruction in Markdown | `SKILL.md` only |
| **Hybrid** | Executable with Markdown documentation | Both files |

**Loading Priority**: workspace → providers → global → built-in

**Registration**: `SkillRegistry.register()` for Python skills; `SkillRegistry.load_md_skills()` for Markdown skills. All skills are registered with the PydanticAI agent via `register_to_agent()`.

### 9.3 Channel Extension

Custom channel handlers implement `ChannelHandler` ABC:

- `setup(connection_config)` — Initialize with config
- `start(context)` / `stop()` — Lifecycle management
- `connect()` / `disconnect()` — For long-connection channels
- `validate_webhook()` / `handle_webhook()` — For webhook channels

Register via `ChannelRegistry.register(channel_type, handler_class)`.

### 9.4 Auth Provider Extension

Custom auth providers extend `AuthProvider` ABC:

- `provider_name` property — Unique identifier
- `authenticate(credential: str) -> AuthResult` — Validate credential

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

---

## 10. Streaming & Real-Time Communication

### 10.1 StreamEvent Pipeline

The agent engine produces `StreamEvent` objects through an async iterator:

| Event Type | Phases/Fields | Purpose |
|---|---|---|
| `LIFECYCLE` | start, end, error, aborted | Run lifecycle markers |
| `ASSISTANT` | content (delta text) | Incremental LLM output |
| `TOOL` | start, update, end + tool name | Tool execution tracking |
| `COMPACTION` | start, end | Context compaction markers |
| `ERROR` | error message | Error reporting |

### 10.2 SSE Transport

- **Format**: Standard Server-Sent Events with `event:` and `data:` fields
- **Event IDs**: Each event has a monotonic ID for `Last-Event-ID` resume support
- **Heartbeat**: Periodic `:heartbeat` comments to keep connection alive
- **Retry**: Configurable reconnect interval via `retry:` field

### 10.3 WebSocket Transport

- Uses the Gateway frame protocol (`ConnectFrame`, `HelloOkFrame`, `RequestFrame`, `ResponseFrame`, `EventFrame`)
- Connection state machine: `CONNECTING → CONNECTED → AUTHENTICATED → DISCONNECTING → DISCONNECTED`
- Multiplexed: single connection supports multiple concurrent request/response pairs

### 10.4 Block Chunker

Configurable via `BlockChunkerConfig`, controls how streaming text is chunked for delivery to channels that require complete blocks (e.g., IM platforms).

---

## 11. Data Storage Model

### 11.1 Sessions

**Format**: JSONL (one JSON object per line)
**Location**: `<workspace>/users/<user_id>/sessions/`
**Files**:
- `<session_id>.jsonl` — Main transcript
- `<session_id>-topic-<thread_id>.jsonl` — Thread transcripts
- `sessions.json` — Session metadata index

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
- `YYYY-MM-DD.md` — Daily memories (date-scoped)
- `MEMORY.md` — Long-term persistent memory

**Search**: Hybrid search combining keyword matching and semantic similarity.

### 11.3 Users

**Format**: JSON
**Location**: `~/.atlasclaw/users.json`
**Content**: Shadow user mappings (`provider:subject` → `user_id` → `ShadowUser`)

### 11.4 Configuration

**Format**: JSON or YAML
**Locations**: 
- Project root (`atlasclaw.json`) — Core configuration
- Workspace root (`<workspace>/atlasclaw.json`) — Workspace configuration
- User home (`~/.atlasclaw/config.json`) — Global user configuration
- Per-user (`<workspace>/users/<user_id>/user_setting.json`) — User-specific settings (channels, preferences)

### 11.5 Token Health

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
- **No external database**: File-system storage only
- **No Redis**: Optional for caching (not required)

---

> **Cross-Reference Index**
>
> | Topic | Document | Section |
> |---|---|---|
> | Per-module API details | [Module Details](./MODULE-DETAILS.MD) | All sections |
> | Code style rules | [Development Spec](./DEVELOPMENT-SPEC.MD) | §1 Code Style |
> | Testing requirements | [Development Spec](./DEVELOPMENT-SPEC.MD) | §6 Testing |
> | Error handling patterns | [Development Spec](./DEVELOPMENT-SPEC.MD) | §3 Error Handling |
> | Provider creation guide | [Provider Guide](./PROVIDER-GUIDE.MD) | Full document |
> | Skill creation guide | [Skill Guide](./SKILL-GUIDE.MD) | Full document |
> | Channel integration | [Channel Guide](./CHANNEL-GUIDE.MD) | Full document |
> | Deployment & operations | [Deployment](./DEPLOYMENT.MD) | Full document |
> | User/developer guide | [Overview](./OVERVIEW.MD) | Full document |
