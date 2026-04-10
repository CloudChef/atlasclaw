# AtlasClaw Architecture

> **Canonical architecture reference for the AtlasClaw enterprise agent framework.**
> All future development and feature changes MUST consult this document.
>
> Related documents:
> - [Module Details](./MODULE-DETAILS.MD) 鈥?per-module API surface and internals
> - [Development Specification](./DEVELOPMENT-SPEC.MD) 鈥?code standards, patterns, and review checklist

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

The framework core handles routing, lifecycle, and coordination. All domain-specific logic lives in **providers** 鈥?modular packages that encapsulate authentication, skills, and configuration for external systems (ITSM, CRM, monitoring, communication platforms). The core never contains business logic for specific integrations.

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
- **Users**: JSON (shadow user store)

**Relational Database** (enterprise mode):
- **SQLite** (default): Zero-config, file-based, auto-creates schema via ORM on startup
- **MySQL** (enterprise): Requires Alembic migrations for DDL/DML management

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

```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹?                       Access Channels                          鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹愨攤
鈹? 鈹俉ebSocket鈹?鈹?  SSE    鈹?鈹俁EST 鈹?鈹侳eishu鈹?鈹侱ingTalk鈹?鈹俉eCom鈹傗攤
鈹? 鈹斺攢鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹?鈹斺攢鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹?鈹斺攢鈹€鈹攢鈹€鈹?鈹斺攢鈹€鈹攢鈹€鈹€鈹?鈹斺攢鈹€鈹€鈹攢鈹€鈹€鈹€鈹?鈹斺攢鈹€鈹攢鈹€鈹樷攤
鈹?      鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹敶鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹粹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹粹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹粹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?
鈹?                  鈻?                                            鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?
鈹? 鈹?                    API Layer                            鈹?  鈹?
鈹? 鈹?  routes.py 路 gateway.py 路 sse.py 路 webhook_dispatch.py  鈹?  鈹?
鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?
鈹?                        鈻?                                      鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹? 鈹?                 Auth Middleware                           鈹? 鈹?
鈹? 鈹? credential extraction 鈫?provider.authenticate()          鈹? 鈹?
鈹? 鈹? 鈫?shadow_store.get_or_create() 鈫?UserInfo                鈹? 鈹?
鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹?                        鈻?                                      鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹? 鈹?                  Agent Engine                            鈹? 鈹?
鈹? 鈹? AgentRunner 路 PromptBuilder 路 CompactionPipeline         鈹? 鈹?
鈹? 鈹? PydanticAI Agent 路 StreamEvent pipeline                  鈹? 鈹?
鈹? 鈹? TokenPool 路 DynamicTokenPolicy 路 AgentInstancePool       鈹? 鈹?
鈹? 鈹? TokenHealthStore 路 TokenHealthInterceptor                鈹? 鈹?
鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹?        鈻?             鈻?                 鈻?                    鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?           鈹?
鈹? 鈹? Tools   鈹? 鈹?  Skills     鈹? 鈹?  Hooks       鈹?           鈹?
鈹? 鈹?16+ built鈹? 鈹?Executable + 鈹? 鈹?17+ lifecycle 鈹?           鈹?
鈹? 鈹?-in tools鈹? 鈹?Markdown +   鈹? 鈹?phases        鈹?           鈹?
鈹? 鈹?profiles 鈹? 鈹?Provider     鈹? 鈹?seq/parallel  鈹?           鈹?
鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?           鈹?
鈹?        鈹?             鈹?                 鈹?                    鈹?
鈹?        鈻?             鈻?                 鈻?                    鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹? 鈹?                Core Infrastructure                       鈹? 鈹?
鈹? 鈹? ConfigManager 路 SkillDeps 路 WorkspaceInitializer         鈹? 鈹?
鈹? 鈹? TenantManager 路 ServiceProviderRegistry                  鈹? 鈹?
鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹?                        鈻?                                      鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹? 鈹?               Data & State Management                    鈹? 鈹?
鈹? 鈹? SessionManager (JSONL) 路 MemoryManager (Markdown)        鈹? 鈹?
鈹? 鈹? ShadowUserStore (JSON) 路 ChannelStore (JSON)             鈹? 鈹?
鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹?                                                                鈹?
鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹? 鈹?              External LLM Providers                      鈹? 鈹?
鈹? 鈹? DeepSeek 路 OpenAI 路 Doubao 路 Kimi 路 Anthropic (via API) 鈹? 鈹?
鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹?
鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
```

### Layer Responsibilities

| Layer | Responsibility | Key Modules |
|---|---|---|
| **Access Channels** | Protocol adaptation, message normalization | `channels/handlers/*` |
| **API Layer** | HTTP routing, SSE streaming, WebSocket frames, webhook dispatch | `api/routes.py`, `api/sse.py`, `api/gateway.py` |
| **Auth Middleware** | Credential extraction, provider authentication, shadow user resolution | `auth/middleware.py`, `auth/strategy.py` |
| **Agent Engine** | LLM orchestration, prompt construction, streaming, context compaction, token management, agent instance pooling | `agent/runner.py`, `agent/prompt_builder.py`, `agent/token_policy.py`, `agent/agent_pool.py`, `core/token_pool.py`, `core/token_health_store.py`, `core/token_interceptor.py` |
| **Tools & Skills** | Executable capabilities exposed to the LLM | `tools/`, `skills/` |
| **Hooks** | Lifecycle interception plus generic runtime event dispatch, per-user hook state, and memory/context sinks | `hooks/system.py`, `hooks/runtime.py` |
| **Core Infrastructure** | Configuration, dependency injection, workspace, tenancy | `core/` |
| **Data & State** | Persistence for sessions, memory, users, channels, token health | `session/`, `memory/`, `auth/shadow_store.py` |

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

```
app/atlasclaw/
鈹溾攢鈹€ main.py                        # FastAPI application factory + lifespan
鈹溾攢鈹€ core/
鈹?  鈹溾攢鈹€ config.py                  # ConfigManager (6-level priority chain)
鈹?  鈹溾攢鈹€ config_schema.py           # Pydantic config models + enums
鈹?  鈹溾攢鈹€ deps.py                    # SkillDeps dataclass (request-scoped DI)
鈹?  鈹溾攢鈹€ workspace.py               # WorkspaceInitializer, UserWorkspaceInitializer
鈹?  鈹溾攢鈹€ provider_registry.py       # ServiceProviderRegistry
鈹?  鈹溾攢鈹€ provider_scanner.py        # ProviderScanner (filesystem discovery)
鈹?  鈹溾攢鈹€ tenant.py                  # TenantManager, TenantConfig, TenantIsolation
鈹?  鈹溾攢鈹€ token_pool.py              # TokenPool, TokenEntry, TokenHealth
鈹?  鈹溾攢鈹€ token_health_store.py      # TokenHealthStore (persistence)
鈹?  鈹斺攢鈹€ token_interceptor.py       # TokenHealthInterceptor (rate-limit headers)
鈹溾攢鈹€ auth/
鈹?  鈹溾攢鈹€ models.py                  # UserInfo, AuthResult, ShadowUser
鈹?  鈹溾攢鈹€ middleware.py              # AuthMiddleware (BaseHTTPMiddleware)
鈹?  鈹溾攢鈹€ strategy.py                # AuthStrategy (cache + resolve pipeline)
鈹?  鈹溾攢鈹€ registry.py                # AuthRegistry (pluggable providers)
鈹?  鈹溾攢鈹€ shadow_store.py            # ShadowUserStore (JSON persistence)
鈹?  鈹溾攢鈹€ config.py                  # AuthConfig, OIDCConfig
鈹?  鈹斺攢鈹€ providers/
鈹?      鈹溾攢鈹€ base.py                # AuthProvider ABC
鈹?      鈹溾攢鈹€ none.py                # NoneProvider (no auth)
鈹?      鈹溾攢鈹€ local.py               # LocalAuthProvider (username/password)
鈹?      鈹溾攢鈹€ oidc_jwt.py            # OIDCJWTProvider (JWT/JWKS validation)
鈹?      鈹斺攢鈹€ oidc_login.py          # OIDCLoginProvider (full OAuth2 + PKCE)

鈹溾攢鈹€ agent/
鈹?  鈹溾攢鈹€ runner.py                  # AgentRunner (main execution loop)
鈹?  鈹溾攢鈹€ stream.py                  # StreamEvent, StreamEventType
鈹?  鈹溾攢鈹€ prompt_builder.py          # PromptBuilder (FULL/MINIMAL/NONE modes)
鈹?  鈹溾攢鈹€ routing.py                 # AgentConfig, DmScope, ToolPolicy
鈹?  鈹溾攢鈹€ agent_definition.py        # AgentLoader, AgentDefinitionParser
鈹?  鈹溾攢鈹€ compaction.py              # CompactionPipeline (context compression)
鈹?  鈹溾攢鈹€ token_policy.py            # DynamicTokenPolicy (session-level token selection)
鈹?  鈹斺攢鈹€ agent_pool.py              # AgentInstancePool (agent instance caching)
鈹溾攢鈹€ api/
鈹?  鈹溾攢鈹€ routes.py                  # REST endpoints, APIContext
鈹?  鈹溾攢鈹€ gateway.py                 # Gateway frames, ConnectionInfo
鈹?  鈹溾攢鈹€ sse.py                     # SSEManager, SSEEvent
鈹?  鈹溾攢鈹€ channels.py                # Channel management endpoints
鈹?  鈹溾攢鈹€ agent_info.py              # Agent info endpoints
鈹?  鈹溾攢鈹€ webhook_dispatch.py        # Webhook routing
鈹?  鈹溾攢鈹€ channel_hooks.py           # Channel-specific hooks
鈹?  鈹溾攢鈹€ response_handler.py        # Response normalization
鈹?  鈹斺攢鈹€ request_orchestrator.py    # Request routing logic
鈹溾攢鈹€ channels/
鈹?  鈹溾攢鈹€ base.py                    # ChannelConfig, ChannelMessage
鈹?  鈹溾攢鈹€ handler.py                 # ChannelHandler ABC
鈹?  鈹溾攢鈹€ models.py                  # ChannelMode, InboundMessage, OutboundMessage, SendResult
鈹?  鈹溾攢鈹€ manager.py                 # ChannelManager
鈹?  鈹溾攢鈹€ registry.py                # ChannelRegistry
鈹?  鈹溾攢鈹€ store.py                   # ChannelStore (persistence)
鈹?  鈹斺攢鈹€ handlers/
鈹?      鈹溾攢鈹€ websocket.py           # WebSocketHandler
鈹?      鈹溾攢鈹€ rest.py                # RESTHandler
鈹?      鈹溾攢鈹€ sse.py                 # SSEHandler
鈹?      鈹溾攢鈹€ feishu.py              # FeishuHandler (multiprocessing isolation)
鈹?      鈹溾攢鈹€ dingtalk.py            # DingTalkHandler (stream mode)
鈹?      鈹斺攢鈹€ wecom.py               # WeComHandler
鈹溾攢鈹€ tools/
鈹?  鈹溾攢鈹€ catalog.py                 # ToolProfile, ToolCatalog, tool groups
鈹?  鈹溾攢鈹€ registration.py            # register_builtin_tools()
鈹?  鈹溾攢鈹€ base.py                    # Tool base classes
鈹?  鈹溾攢鈹€ filesystem/                # legacy file tools (disabled by default)
鈹?  鈹溾攢鈹€ runtime/                   # legacy runtime tools (disabled by default)
鈹?  鈹溾攢鈹€ memory/                    # memory_search, memory_get
鈹?  鈹溾攢鈹€ sessions/                  # list, history, send, spawn, status, subagents
鈹?  鈹溾攢鈹€ web/                       # web_search, web_fetch
鈹?  鈹溾攢鈹€ ui/                        # browser
鈹?  鈹斺攢鈹€ providers/                 # instance_tools (list/select provider instances)
鈹溾攢鈹€ skills/
鈹?  鈹溾攢鈹€ registry.py                # SkillRegistry, SkillMetadata, MdSkillEntry
鈹?  鈹溾攢鈹€ frontmatter.py             # parse_frontmatter(), FrontmatterResult
鈹?  鈹斺攢鈹€ loader.py                  # SkillLoader
鈹溾攢鈹€ session/
鈹?  鈹溾攢鈹€ context.py                 # SessionKey, SessionScope, SessionMetadata, TranscriptEntry
鈹?  鈹溾攢鈹€ manager.py                 # SessionManager (JSONL persistence)
鈹?  鈹斺攢鈹€ queue.py                   # SessionQueue, QueueMode
鈹溾攢鈹€ memory/
鈹?  鈹溾攢鈹€ manager.py                 # MemoryManager, MemoryEntry, MemoryType
鈹?  鈹斺攢鈹€ hybrid_search.py           # Hybrid search (keyword + semantic)
鈹溾攢鈹€ models/
鈹?  鈹溾攢鈹€ providers.py               # ProviderConfig, ModelDefinition
鈹?  鈹斺攢鈹€ failover.py                # AuthProfile, ModelFailoverConfig
鈹溾攢鈹€ hooks/
鈹?  鈹斺攢鈹€ system.py                  # HookSystem, HookPhase, HookDefinition
鈹溾攢鈹€ workflow/
鈹?  鈹溾攢鈹€ engine.py                  # WorkflowEngine, WorkflowStep, StepStatus
鈹?  鈹斺攢鈹€ orchestrator.py            # Orchestration logic
鈹溾攢鈹€ messages/
鈹?  鈹溾攢鈹€ handler.py                 # InboundMessage, OutboundMessage, MessageType
鈹?  鈹斺攢鈹€ command.py                 # CommandParser, ParsedCommand, CommandDefinition
鈹溾攢鈹€ media/
鈹?  鈹溾攢鈹€ link_extractor.py          # URL extraction from text
鈹?  鈹溾攢鈹€ tts.py                     # Text-to-speech
鈹?  鈹斺攢鈹€ understanding.py           # Media content understanding
鈹斺攢鈹€ providers/                     # Built-in provider integrations
    鈹斺攢鈹€ <provider>/
        鈹溾攢鈹€ PROVIDER.md            # Provider documentation + LLM context
        鈹斺攢鈹€ skills/                # Provider-specific skills

app/frontend/
鈹溾攢鈹€ index.html                     # Main chat interface
鈹溾攢鈹€ channels.html                  # Channel management UI
鈹溾攢鈹€ package.json                   # esbuild-based build
鈹溾攢鈹€ scripts/
鈹?  鈹溾攢鈹€ main.js                    # Application entry point
鈹?  鈹溾攢鈹€ api-client.js              # HTTP client wrapper
鈹?  鈹溾攢鈹€ channel-manager.js         # Channel connection management
鈹?  鈹溾攢鈹€ chat-ui.js                 # Chat UI components
鈹?  鈹溾攢鈹€ config.js                  # Frontend configuration
鈹?  鈹溾攢鈹€ i18n.js                    # Internationalization (zh-CN, en-US)
鈹?  鈹溾攢鈹€ session-manager.js         # Session lifecycle
鈹?  鈹斺攢鈹€ stream-handler.js          # SSE stream handling
鈹溾攢鈹€ styles/                        # CSS stylesheets
鈹溾攢鈹€ static/                        # Static assets
鈹斺攢鈹€ locales/                       # i18n translation files

tests/
鈹溾攢鈹€ atlasclaw/                     # Python test suite (~48 test files)
鈹?  鈹溾攢鈹€ conftest.py                # Shared fixtures
鈹?  鈹溾攢鈹€ test_agent.py              # Agent engine tests
鈹?  鈹溾攢鈹€ test_config.py             # Configuration tests
鈹?  鈹溾攢鈹€ test_session.py            # Session management tests
鈹?  鈹斺攢鈹€ ...
鈹斺攢鈹€ frontend/                      # JavaScript tests (~6 test files)

providers/                         # Provider repository (code/templates)
鈹斺攢鈹€ <provider_name>/
    鈹溾攢鈹€ PROVIDER.md
    鈹溾攢鈹€ auth/
    鈹溾攢鈹€ channels/
    鈹斺攢鈹€ skills/

<workspace>/                       # Runtime workspace (default: ./.atlasclaw)
鈹溾攢鈹€ agents/
鈹?  鈹斺攢鈹€ main/
鈹?      鈹溾攢鈹€ SOUL.md                # Agent capabilities and behavior
鈹?      鈹溾攢鈹€ IDENTITY.md            # Agent personality
鈹?      鈹溾攢鈹€ USER.md                # User interaction patterns
鈹?      鈹斺攢鈹€ MEMORY.md              # Memory strategy
鈹斺攢鈹€ users/
    鈹斺攢鈹€ <user_id>/
        鈹溾攢鈹€ user_setting.json      # User-specific config (channels, preferences)
        鈹溾攢鈹€ sessions/              # JSONL session transcripts
        鈹斺攢鈹€ memory/                # Markdown memory files
```

---

## 4. Application Bootstrap & Lifespan

The application starts via `app/atlasclaw/main.py`:

```
create_app() 鈫?FastAPI(lifespan=lifespan)
```

### Initialization Sequence (Startup)

The `lifespan()` async context manager executes this ordered sequence:

1. **Load configuration** 鈥?`ConfigManager.load()` reads `atlasclaw.json`/`atlasclaw.yaml` with environment variable interpolation
2. **Initialize workspace** 鈥?`WorkspaceInitializer.initialize()` creates the `.atlasclaw/` directory tree and copies the default `agents/main/` files from the repo template directory when they are missing
3. **Register built-in channel handlers** 鈥?Feishu, DingTalk, WeCom handlers registered in `ChannelRegistry`
4. **Initialize ChannelManager** 鈥?Creates `ChannelManager` with workspace path
5. **Scan providers** 鈥?`ProviderScanner` walks the providers directory (outside workspace), discovers auth and channel extensions
6. **Load agent definitions** 鈥?`AgentLoader` reads `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md` from `agents/main/`
7. **Initialize TokenPool** 鈥?Creates `TokenPool` from `model.tokens` config, restores health state from `TokenHealthStore`
8. **Initialize SessionManager** 鈥?Creates session manager with configured reset mode
9. **Initialize Database** 鈥?SQLite (auto-create via ORM) or MySQL (Alembic migrations)
10. **Load TokenPool** 鈥?Creates `TokenPool` from merged JSON + DB token configs (DB takes precedence on same token_id)
11. **Load ServiceProviderRegistry** 鈥?Reads provider templates from disk, merges JSON + DB instance configs (DB takes precedence on same provider_type.instance_name)
10. **Register built-in tools** 鈥?`register_builtin_tools()` with the configured `ToolProfile`
11. **Load skills** 鈥?From 4 sources in priority order: workspace 鈫?workspace providers 鈫?global 鈫?built-in
12. **Create AgentInstancePool** 鈥?Creates pool for agent instance caching with concurrency control
13. **Create PydanticAI Agent** 鈥?Instantiates the agent with resolved model configuration
14. **Register skills to agent** 鈥?`SkillRegistry.register_to_agent()`
15. **Create AgentRunner** 鈥?With agent, session manager, prompt builder, compaction pipeline, hook system, session queue, token policy, agent pool, token interceptor
16. **Set up APIContext** 鈥?Global context holder for all API routes
17. **Start channel connections** 鈥?Initialize active channel connections

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
鈹?
鈹溾攢 1. AuthMiddleware.dispatch()
鈹?    鈹溾攢 Extract credential (header 鈫?cookie 鈫?bearer)
鈹?    鈹溾攢 AuthStrategy.resolve_user(credential)
鈹?    鈹?    鈹溾攢 Check TTL cache (300s default)
鈹?    鈹?    鈹溾攢 AuthProvider.authenticate(credential) 鈫?AuthResult
鈹?    鈹?    鈹斺攢 ShadowUserStore.get_or_create() 鈫?ShadowUser 鈫?UserInfo
鈹?    鈹斺攢 Attach UserInfo to request.state.user
鈹?
鈹溾攢 2. Route handler (api/routes.py)
鈹?    鈹溾攢 Parse AgentRunRequest (session_key, message, model, timeout)
鈹?    鈹溾攢 Resolve session key via SessionScope
鈹?    鈹斺攢 Build SkillDeps (user_info, session, memory managers, abort_signal)
鈹?
鈹溾攢 3. AgentRunner.run() 鈫?AsyncIterator[StreamEvent]
鈹?    鈹溾攢 Emit lifecycle_start
鈹?    鈹溾攢 Select/pin token via DynamicTokenPolicy.get_or_select_session_token()
鈹?    鈹溾攢 Get or create agent instance from AgentInstancePool
鈹?    鈹溾攢 Load session transcript (JSONL)
鈹?    鈹溾攢 Build message history from transcript
鈹?    鈹溾攢 PromptBuilder.build() 鈫?system prompt
鈹?    鈹溾攢 Trigger BEFORE_PROMPT_BUILD hook
鈹?    鈹溾攢 CompactionPipeline (if context exceeds threshold)
鈹?    鈹溾攢 Check SessionQueue for steering messages
鈹?    鈹溾攢 PydanticAI Agent.run_stream() 鈫?model inference
鈹?    鈹?    鈹溾攢 Yield assistant deltas (StreamEvent.ASSISTANT)
鈹?    鈹?    鈹溾攢 Execute tool calls (StreamEvent.TOOL start/end)
鈹?    鈹?    鈹溾攢 TokenHealthInterceptor.on_response() 鈫?update rate limits
鈹?    鈹?    鈹斺攢 Enforce max_tool_calls and timeout_seconds
鈹?    鈹溾攢 Persist transcript entries (append to JSONL)
鈹?    鈹斺攢 Emit lifecycle_end
鈹?
鈹斺攢 4. SSE Response
      鈹溾攢 SSEManager.create_stream() 鈫?event: lifecycle, data: {phase: "start"}
      鈹溾攢 event: assistant, data: {content: "delta text"}
      鈹溾攢 event: tool, data: {phase: "start", tool: "web_search"}
      鈹溾攢 event: tool, data: {phase: "end", tool: "web_search", result: "..."}
      鈹溾攢 event: assistant, data: {content: "more text"}
      鈹斺攢 event: lifecycle, data: {phase: "end"}
```

### 5.2 Channel Inbound Message Flow

```
External Platform (e.g., Feishu)
鈹?
鈹溾攢 1. ChannelHandler.handle_webhook() or message callback
鈹?    鈹斺攢 Parse platform-specific payload 鈫?InboundMessage
鈹?
鈹溾攢 2. ChannelManager.receive_message()
鈹?    鈹溾攢 Resolve user identity from channel context
鈹?    鈹溾攢 Determine session key via DmScope routing
鈹?    鈹斺攢 CommandParser.parse() 鈥?check for /commands
鈹?
鈹溾攢 3. AgentRunner.run() 鈫?stream events
鈹?
鈹斺攢 4. ChannelManager.send_message()
      鈹斺攢 ChannelHandler.send() 鈫?platform-specific API call
```

### 5.3 WebSocket Gateway Flow

```
WebSocket Connection
鈹?
鈹溾攢 1. ConnectFrame 鈫?validate credentials
鈹溾攢 2. HelloOkFrame 鈫?acknowledge connection
鈹溾攢 3. RequestFrame 鈫?agent run (similar to REST flow)
鈹溾攢 4. EventFrame 鈫?streaming events pushed to client
鈹斺攢 5. Disconnect 鈫?cleanup connection state
```

---

## 6. Subsystem Interconnections

### Dependency Graph

```
                    鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
                    鈹? main   鈹?(orchestrator)
                    鈹斺攢鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹?
          鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
          鈻?             鈻?             鈻?
    鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
    鈹?  core   鈹? 鈹?  auth   鈹? 鈹?  channels   鈹?
    鈹?config   鈹? 鈹?middleware鈹? 鈹?  manager    鈹?
    鈹?deps     鈹? 鈹?strategy 鈹? 鈹?  registry   鈹?
    鈹?workspace鈹? 鈹?providers鈹? 鈹?  handlers   鈹?
    鈹?tenant   鈹? 鈹?shadow   鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹?
    鈹?registry 鈹? 鈹斺攢鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹?        鈹?
    鈹斺攢鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹?      鈹?              鈹?
         鈹?            鈻?              鈻?
         鈹?     鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?   鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
         鈹溾攢鈹€鈹€鈹€鈹€鈻衡攤  agent   鈹傗梽鈹€鈹€鈹€鈹?  api    鈹?
         鈹?     鈹?runner   鈹?   鈹?routes   鈹?
         鈹?     鈹?prompt   鈹?   鈹?sse      鈹?
         鈹?     鈹?stream   鈹?   鈹?gateway  鈹?
         鈹?     鈹斺攢鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹?   鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
         鈹?          鈹?
         鈹?    鈹屸攢鈹€鈹€鈹€鈹€鈹尖攢鈹€鈹€鈹€鈹€鈹?
         鈹?    鈻?    鈻?    鈻?
         鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹愨攲鈹€鈹€鈹€鈹€鈹€鈹愨攲鈹€鈹€鈹€鈹€鈹€鈹€鈹?
         鈹?鈹倀ools 鈹傗攤skill鈹傗攤hooks 鈹?
         鈹?鈹俢atlog鈹傗攤regis鈹傗攤system鈹?
         鈹?鈹斺攢鈹€鈹攢鈹€鈹€鈹樷敂鈹€鈹€鈹攢鈹€鈹樷敂鈹€鈹€鈹€鈹€鈹€鈹?
         鈹?   鈹?      鈹?
         鈹?   鈻?      鈻?
         鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
         鈹斺柡鈹? session       鈹?
           鈹? memory        鈹?
           鈹? messages      鈹?
           鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
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
| `SkillRegistry` | PydanticAI Agent | `register_to_agent()` 鈥?wraps skills as agent tools |
| `ServiceProviderRegistry` | `SkillRegistry` | `register_skills_to()` 鈥?provider skills with instance awareness |
| `ChannelManager` | `AgentRunner` | `receive_message()` 鈫?`run()` |
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

```
Credential 鈫?AuthProvider.authenticate() 鈫?AuthResult
                                              鈹?
                                              鈻?
                               ShadowUserStore.get_or_create()
                                              鈹?
                                              鈻?
                                     ShadowUser 鈫?UserInfo
```

**Credential Sources** (checked in order):
1. `CloudChef-Authenticate` header
2. `Authorization: Bearer <token>` header
3. `CloudChef-Authenticate` cookie

**Auth Providers** (configured in `auth.provider`):

*Primary Providers* (for user login, configured via `auth.provider`):
- `none` 鈥?No authentication; all requests get default user
- `local` 鈥?Username/password authentication against local user database

*Secondary Providers* (always available for specific use cases):
- `oidc_jwt` 鈥?JWT validation via JWKS for API authentication (bearer tokens)
- `oidc_login` 鈥?Full OAuth2 + PKCE authorization code flow for browser SSO

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
- **Tenant isolation**: `TenantIsolation` provides path-based compartmentalization 鈥?session keys, memory paths, and storage paths are prefixed with `tenant_id`

### 8.3 Skip Paths (No Auth Required)

`/health`, `/ping`, `/favicon.ico`, `/docs`, `/openapi.json`, static assets (`/static/`, `/styles/`, `/scripts/`, `/locales/`), SSO endpoints (`/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`, `/api/auth/me`)

### 8.4 Multi-Tenant Isolation

```
TenantIsolation.get_session_path(tenant_id)  鈫?<workspace>/tenants/<tenant_id>/sessions/
TenantIsolation.get_memory_path(tenant_id)   鈫?<workspace>/tenants/<tenant_id>/memory/
TenantIsolation.isolate_session_key(key, id) 鈫?<tenant_id>:<session_key>
```

Tenant resolution order: explicit session key 鈫?user header 鈫?config default 鈫?"default"

Quota enforcement: `TenantManager.check_quota()` validates `max_sessions`, `max_agents`, `max_memory_entries`, `max_concurrent_runs`.

---

## 9. Extension Points

AtlasClaw provides four main extension mechanisms:

### 9.1 Provider Extension

Providers are the primary extension point. A provider is a directory containing:

```
<provider>/
鈹溾攢鈹€ PROVIDER.md          # Documentation + LLM context (frontmatter)
鈹斺攢鈹€ skills/
    鈹溾攢鈹€ skill-name/
    鈹?  鈹溾攢鈹€ SKILL.md     # Skill documentation + frontmatter
    鈹?  鈹斺攢鈹€ handler.py   # Executable skill (optional)
    鈹斺攢鈹€ ...
```

**Discovery**: `ProviderScanner` walks the providers directory at startup. Provider directories can also contribute auth providers and channel handlers.

**Instance Awareness**: A single provider type (e.g., "jira") can have multiple instances (e.g., "production", "staging"). `ServiceProviderRegistry` wraps skill handlers with automatic instance selection logic.

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

**Loading Priority**: workspace 鈫?providers 鈫?global 鈫?built-in

**Registration**: `SkillRegistry.register()` for Python skills; `SkillRegistry.load_md_skills()` for Markdown skills. All skills are registered with the PydanticAI agent via `register_to_agent()`.

### 9.3 Channel Extension

Custom channel handlers implement `ChannelHandler` ABC:

- `setup(connection_config)` 鈥?Initialize with config
- `start(context)` / `stop()` 鈥?Lifecycle management
- `connect()` / `disconnect()` 鈥?For long-connection channels
- `validate_webhook()` / `handle_webhook()` 鈥?For webhook channels

Register via `ChannelRegistry.register(channel_type, handler_class)`.

### 9.4 Auth Provider Extension

Custom auth providers extend `AuthProvider` ABC:

- `provider_name` property 鈥?Unique identifier
- `authenticate(credential: str) -> AuthResult` 鈥?Validate credential

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
- `HookRuntime` 鈥?dispatches typed runtime events to registered handlers
- `HookStateStore` 鈥?persists per-user, per-module hook state
- `MemorySink` 鈥?writes confirmed items into long-term memory files
- `ContextSink` 鈥?exposes confirmed items for future prompt/context integration

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
- Connection state machine: `CONNECTING 鈫?CONNECTED 鈫?AUTHENTICATED 鈫?DISCONNECTING 鈫?DISCONNECTED`
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

- `ToolNecessityGate` - classifies whether direct answering is allowed for the current turn
- `CapabilityMatcher` - maps required capability classes to currently available runtime tools
- `Mandatory Tool Enforcement` - blocks the final answer if the runtime requires tools and no grounded tool path succeeds

Phase 1 is classifier-first, not keyword-first:

- the runtime accepts an explicit classifier decision when available
- model-backed classification is an opt-in backend and is disabled by default
- invalid classifier output is discarded
- if no classifier is available, the gate falls back to a neutral direct-answer default
- hard-coded business keyword tables are intentionally not the platform contract

The runtime emits tool-gate and enforcement events into Hook Runtime so downstream consumers can inspect why a turn was allowed to answer directly, required tools, or was blocked for missing grounding.
## 11. Data Storage Model

### 11.1 Sessions

**Format**: JSONL (one JSON object per line)
**Location**: `<workspace>/users/<user_id>/sessions/`
**Files**:
- `<session_id>.jsonl` 鈥?Main transcript
- `<session_id>-topic-<thread_id>.jsonl` 鈥?Thread transcripts
- `sessions.json` 鈥?Session metadata index

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
- `YYYY-MM-DD.md` 鈥?Daily memories (date-scoped)
- `MEMORY.md` 鈥?Long-term persistent memory

**Search**: Hybrid search combining keyword matching and semantic similarity.

Confirmed runtime output may also be promoted into timestamped long-term memory
documents such as `memory_<timestamp>.md` under the same per-user memory
directory.

### 11.3 Hook Runtime State

**Format**: JSONL
**Location**: `<workspace>/users/<user_id>/hooks/<module_name>/`
**Files**:
- `events.jsonl` 鈥?Observed runtime lifecycle events
- `pending.jsonl` 鈥?Items awaiting explicit confirmation or rejection
- `decisions.jsonl` 鈥?Confirmation/rejection history

**Isolation**:
- State is scoped by authenticated AtlasClaw `user_id`
- Hook modules do not share files unless they intentionally use the same module name

### 11.4 Heartbeat Runtime State

**Format**: JSON / JSONL
**Location**: `<workspace>/users/<user_id>/heartbeat/`
**Files**:
- `jobs.json` 鈥?Registered heartbeat job definitions
- `state.json` 鈥?Durable heartbeat job state snapshots
- `events.jsonl` 鈥?Local heartbeat runtime event log

Heartbeat state is intentionally separate from Hook Runtime state:

- heartbeat owns scheduling, retry, health, and delivery target resolution
- hooks consume emitted heartbeat events downstream

### 11.4 Users

**Format**: JSON
**Location**: `~/.atlasclaw/users.json`
**Content**: Shadow user mappings (`provider:subject` 鈫?`user_id` 鈫?`ShadowUser`)

### 11.5 Configuration

**Format**: JSON or YAML
**Locations**: 
- Project root (`atlasclaw.json`) 鈥?Core configuration
- Workspace root (`<workspace>/atlasclaw.json`) 鈥?Workspace configuration
- User home (`~/.atlasclaw/config.json`) 鈥?Global user configuration
- Per-user (`<workspace>/users/<user_id>/user_setting.json`) 鈥?User-specific settings (channels, preferences)

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
- `GET/POST /agent-configs` 鈥?Agent CRUD
- `GET/POST /token-configs` 鈥?Token CRUD
- `GET/POST /users` 鈥?User CRUD
- `GET/POST /provider-configs` 鈥?Provider instance CRUD

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
- `TokenModel.api_key` 鈥?LLM API keys in database
- `ServiceProviderConfigModel.config` 鈥?Provider credentials and settings
- `ChannelModel.config` 鈥?Channel connection credentials

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
> | Code style rules | [Development Spec](./DEVELOPMENT-SPEC.MD) | 搂1 Code Style |
> | Testing requirements | [Development Spec](./DEVELOPMENT-SPEC.MD) | 搂6 Testing |
> | Error handling patterns | [Development Spec](./DEVELOPMENT-SPEC.MD) | 搂3 Error Handling |
> | Provider creation guide | [Provider Guide](./PROVIDER-GUIDE.MD) | Full document |
> | Skill creation guide | [Skill Guide](./SKILL-GUIDE.MD) | Full document |
> | Channel integration | [Channel Guide](./CHANNEL-GUIDE.MD) | Full document |
> | Deployment & operations | [Deployment](./DEPLOYMENT.MD) | Full document |
> | User/developer guide | [Overview](./OVERVIEW.MD) | Full document |
