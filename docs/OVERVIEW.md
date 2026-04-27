# AtlasClaw User Guide

## Table of Contents

1. [Overview](#1-overview)
2. [Quick Start](#2-quick-start)
3. [Configuration](#3-configuration)
4. [Starting the Service](#4-starting-the-service)
5. [Web UI](#5-web-ui)
6. [REST API](#6-rest-api)
7. [Skills System](#7-skills-system)
8. [Providers Integration](#8-providers-integration)
9. [Authentication](#9-authentication)
   - [9.1 No Authentication](#91-no-authentication-development--testing)
   - [9.2 API Key Authentication](#92-api-key-authentication)
   - [9.3 OIDC Authentication](#93-oidc-authentication)
   - [9.4 OAuth2 SSO Authentication (Browser Login)](#94-oauth2-sso-authentication-browser-login)
10. [Advanced Configuration](#10-advanced-configuration)
11. [Development & Testing](#11-development--testing)
12. [Architecture Reference](#12-architecture-reference)

---

## 1. Overview

**AtlasClaw** is an enterprise-grade AI Agent framework that lets employees interact with multiple enterprise systems through a single conversational AI interface. Instead of switching between separate consoles, dashboards, and portals, users can use natural language to trigger workflows, query operational data, and complete cross-system tasks from one entry point.

### Core Design Principles

| Principle | Description |
|-----------|-------------|
| **Thin Core, Rich Providers** | Reusable Agent logic at the center; platform-specific integrations in Providers |
| **Permission Inheritance** | Every action runs under the authenticated user's real permissions — no RBAC bypass |
| **Multi-Channel** | Web UI, embedded iframe, chat platforms (Slack, Teams), and programmatic webhooks |
| **Skill-First** | All capabilities exposed through a composable, self-documenting Skills system |

### Deployment Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Embedded** | Integrated as a module inside an existing enterprise portal (e.g., iframe) | Add AI Agent capability to an existing product quickly |
| **Standalone** | Runs as an independent AI Agent platform connecting multiple systems | Unified AI entry point across the enterprise |

### Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Web Framework | FastAPI ≥ 0.109 | REST API & WebSocket |
| ASGI Server | Uvicorn ≥ 0.27 | Async server |
| Data Validation | Pydantic ≥ 2.6 | Models & configuration |
| AI Framework | PydanticAI ≥ 0.0.14 | Agent runtime & tool calling |
| LLM Client | OpenAI ≥ 1.12 | LLM API calls (OpenAI-compatible) |
| Frontend UI | DeepChat | Chat interface |

---

## 2. Quick Start

### Requirements

- Python 3.11+
- Access to an LLM provider (DeepSeek, Doubao, Kimi/Anthropic, OpenAI, etc.)

### Install Dependencies

```bash
# Navigate to AtlasClaw-Core
cd AtlasClaw-Core

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt
```

### Minimal Setup

**Step 1 — Create the configuration file**

```bash
cp atlasclaw.json.example atlasclaw.json
```

Edit `atlasclaw.json` to select your LLM provider (see [Section 3.1](#31-model-configuration)):

```json
{
  "model": {
    "primary": "deepseek/deepseek-chat",
    "temperature": 0.7,
    "providers": {
      "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key": "${DEEPSEEK_API_KEY}",
        "api_type": "openai"
      }
    }
  }
}
```

**Step 2 — Create `.env` and set API keys**

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
# DeepSeek (OpenAI-compatible)
DEEPSEEK_API_KEY=your-api-key

# Or Doubao (OpenAI-compatible)
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
DOUBAO_API_KEY=your-api-key

# Or Kimi (Anthropic-compatible)
ANTHROPIC_BASE_URL=https://api.moonshot.cn/anthropic
ANTHROPIC_API_KEY=your-api-key
```

**Step 3 — Start the service**

```bash
uvicorn app.atlasclaw.main:app --reload --host 0.0.0.0 --port 8000
```

**Step 4 — Open the Web UI**

Navigate to `http://127.0.0.1:8000` in your browser.

---

## 3. Configuration

The main configuration file is `atlasclaw.json`, located in the project root. All `${VAR_NAME}` placeholders are automatically expanded from environment variables at runtime.

> **Configuration priority (high → low):**
> 1. Runtime overrides (via `config_manager.set()`)
> 2. Environment variables (`ATLASCLAW_*` prefix)
> 3. `atlasclaw.json`
> 4. Built-in defaults (`core/config_schema.py`)

### 3.1 Model Configuration

```json
{
  "model": {
    "primary": "deepseek/deepseek-chat",
    "fallbacks": ["doubao/doubao-seed-1-6-lite-251015"],
    "temperature": 0.7,
    "max_tokens": null,
    "providers": {
      "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key": "${DEEPSEEK_API_KEY}",
        "api_type": "openai"
      },
      "doubao": {
        "base_url": "${DOUBAO_BASE_URL}",
        "api_key": "${DOUBAO_API_KEY}",
        "api_type": "openai"
      },
      "kimi": {
        "base_url": "${ANTHROPIC_BASE_URL}",
        "api_key": "${ANTHROPIC_API_KEY}",
        "api_type": "anthropic"
      }
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `primary` | Primary model in `provider/model-name` format |
| `fallbacks` | Ordered fallback models used when the primary fails |
| `temperature` | Generation temperature, 0–2, default `0.7` |
| `api_type` | `openai` (default, OpenAI-compatible) or `anthropic` |

### 3.2 Service Providers Configuration

Configure enterprise system instances the Agent can integrate with:

```json
{
  "service_providers": {
    "ticketing": {
      "prod": {
        "base_url": "https://ticketing.corp.com",
        "username": "${TICKETING_USERNAME}",
        "password": "${TICKETING_PROD_TOKEN}",
        "api_version": "2",
        "default_project": "PROJ"
      },
      "dev": {
        "base_url": "https://ticketing-dev.corp.com",
        "username": "${TICKETING_USERNAME}",
        "password": "${TICKETING_DEV_TOKEN}",
        "api_version": "2",
        "default_project": "DEV"
      }
    }
  }
}
```

Format: `{ provider_type: { instance_name: { connection_params } } }`

Multiple instances per provider type are supported. Instance names (`prod`, `dev`) are shown to users at runtime.

### 3.3 Webhook Configuration

Configure inbound webhooks for provider-qualified skills already loaded from
`providers_root`:

```json
{
  "providers_root": "../atlasclaw-providers/providers",
  "webhook": {
    "enabled": true,
    "header_name": "X-AtlasClaw-SK",
    "systems": [
      {
        "system_id": "external-review",
        "enabled": true,
        "sk_env": "ATLASCLAW_WEBHOOK_SK_EXTERNAL_REVIEW",
        "default_agent_id": "main",
        "allowed_skills": ["external-review:review"]
      }
    ]
  }
}
```

### 3.4 Authentication Configuration

Configured under the `auth` section. See [Section 9](#9-authentication) for full details.

---

## 4. Starting the Service

### Development Mode (with hot reload)

```bash
uvicorn app.atlasclaw.main:app --reload --host 0.0.0.0 --port 8000
```

### Production Mode

```bash
uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Using Gunicorn

```bash
gunicorn app.atlasclaw.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Successful Startup Log

```
[AtlasClaw] Registered 12 built-in tools
[AtlasClaw] Agent created with model: openai:deepseek-chat
[AtlasClaw] Application started successfully
[AtlasClaw] Session storage: ~/.atlasclaw/agents
[AtlasClaw] Skills loaded: 5 executable, 3 markdown
```

### Environment Variables for Deployment

| Variable | Description | Default |
|----------|-------------|---------|
| `ATLASCLAW_CONFIG` | Path to `atlasclaw.json` | `atlasclaw.json` |
| `UNICLAW_API_BASE_URL` | Public URL served to the frontend (for cross-origin deployment) | — |
| `CORS_ORIGINS` | Comma-separated allowed CORS origins | localhost origins only |
| `LOG_LEVEL` | Logging level (`DEBUG` / `INFO` / `WARNING`) | `INFO` |
| `NO_PROXY` | Bypass proxy for internal addresses | — |

---

## 5. Web UI

### Access URL

```
http://127.0.0.1:8000
```

### Features

- **Chat Interface** — DeepChat-powered UI with streaming responses
- **Real-time Streaming** — Agent thinking and tool execution steps shown live via SSE
- **Multi-language** — Chinese / English toggle (`app/frontend/locales/zh-CN.json` / `en-US.json`)
- **Session Persistence** — Conversation history saved automatically to `~/.atlasclaw/agents/`

### Runtime Configuration

The frontend reads its API base URL from `/config.json` at startup. For cross-origin deployments (e.g., embedded in another platform), set the following in `.env`:

```bash
# Tells the frontend where to send API requests
UNICLAW_API_BASE_URL=http://192.168.88.4:8000
# Allow the embedding platform's origin
CORS_ORIGINS=http://192.168.88.4:8000,http://172.16.0.93
```

### Frontend Development

Source files are served directly — no build step required in development. Edit `app/frontend/scripts/*.js` and refresh.

**Production bundle:**

```bash
cd app/frontend
npm install
npm run build      # production bundle
npm run build:dev  # development bundle with sourcemaps
```

---

## 6. REST API

After the service starts, interactive API docs are available at:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

All API paths are prefixed with `/api`.

### 6.1 Session Management

Sessions track conversation context. A `session_key` is returned on creation and used for all subsequent calls.

AtlasClaw now treats session ownership and chat threading as separate concerns:

- `user_id` in the canonical `SessionKey` identifies the AtlasClaw user that owns the session bucket
- `channel`, `account_id`, `chat_type`, and `peer_id` identify the external conversation scope
- `thread_id` identifies an independent chat thread inside the same scope
- Session metadata and transcripts are stored under `workspace/users/<user_id>/sessions/`

`GET /api/sessions` returns all sessions owned by the current authenticated user across all channels, not just the web UI.

#### Create a Session

```http
POST /api/sessions
Content-Type: application/json

{
  "agent_id": "main",
  "channel": "api",
  "chat_type": "dm",
  "scope": "main"
}
```

This endpoint returns the stable scope session. It is useful when you want deterministic reuse of a conversation key.

#### Create a New Chat Thread

```http
POST /api/sessions/threads
Content-Type: application/json

{
  "agent_id": "main",
  "channel": "web",
  "chat_type": "dm"
}
```

This endpoint always creates a new independent thread and returns a distinct `session_key`.

#### Get / Reset / Delete a Session

```http
GET    /api/sessions/{session_key}
POST   /api/sessions/{session_key}/reset    Body: { "archive": true }
DELETE /api/sessions/{session_key}
```

Direct session operations enforce ownership. A user cannot inspect, reset, compact, queue, or delete another user's session key.

### 6.2 Agent Execution

#### Start an Agent Run

```http
POST /api/agent/run
Content-Type: application/json

{
  "session_key": "main:api:dm:default",
  "message": "List all open work items assigned to me",
  "timeout_seconds": 600
}
```

Returns a `run_id` used to subscribe to the SSE stream.

#### Subscribe to SSE Stream

```http
GET /api/agent/runs/{run_id}/stream
Accept: text/event-stream
```

| Event Type | Description |
|------------|-------------|
| `lifecycle` | Run phase transitions (start / end / error) |
| `assistant` | Streamed assistant response content |
| `tool` | Tool call events (call / result) |
| `error` | Error details |

#### Manage a Run

```http
GET  /api/agent/runs/{run_id}         # Query run status
POST /api/agent/runs/{run_id}/abort   # Abort a running run
```

### 6.3 Skills API

```http
GET  /api/skills                      # List all registered skills

POST /api/skills/execute
Content-Type: application/json

{
  "skill_name": "web_search",
  "args": { "query": "AtlasClaw documentation" }
}
```

### 6.4 Health Check

```http
GET /api/health
# Response: { "status": "healthy" }
```

---

## 7. Skills System

Skills are the core extension mechanism — they define everything the Agent can do.

### 7.1 Skill Types

| Type | Description | Use Case |
|------|-------------|----------|
| **Executable** | Python handler function registered as an Agent tool | API calls, data operations |
| **Markdown (MD)** | `SKILL.md` injected into the Agent's system prompt | Knowledge, process guidelines |
| **Hybrid** | Executable handler + rich `SKILL.md` documentation | Complex integrations |

### 7.2 Skill Loading Sources

AtlasClaw exposes capabilities from system-level runtime code, standalone skill
packages, and provider-owned skill packages.

| Source | Location | Notes |
|--------|----------|-------|
| System-level skills and tools | Core runtime (`app/atlasclaw/skills`, `app/atlasclaw/tools`) | Built-in capabilities owned by AtlasClaw core |
| Standalone skills | `skills_root` if configured | Shared non-provider skill packages |
| Provider-owned skills | `<providers_root>/<provider>/skills/` | Skills packaged with one provider |

### 7.3 Creating a Markdown Skill

The simplest way to extend the Agent — no Python required.

**Directory structure:**

```
skills/
└── my-skill/
    └── SKILL.md
```

**SKILL.md format:**

```markdown
---
name: my-skill-name
description: >
  One-sentence description of what this skill does and what phrases trigger it.
  Example: Use when the user asks about the weather.
category: utility
version: "1.0"
author: your-name
---

# Skill Title

## When to Use

Describe the scenarios where this skill applies.

## Execution Steps

Step-by-step instructions for how the Agent should perform this task...
```

### 7.4 Creating an Executable Skill

For skills that call external APIs or run code:

```
skills/my-skill/
├── SKILL.md          # Metadata + documentation
└── scripts/
    ├── handler.py    # Main implementation
    └── _utils.py     # Helper functions (optional)
```

`handler.py` exposes a `handler` async function:

```python
from pydantic_ai import RunContext
from app.atlasclaw.core.deps import SkillDeps

SKILL_METADATA = {
    "name": "my_skill",
    "description": "Brief description",
    "category": "utility",
}

async def handler(ctx: RunContext[SkillDeps], param: str) -> dict:
    """Skill implementation."""
    return {"result": f"Processed: {param}"}
```

### 7.5 Built-in Tools

Registered automatically at startup:

| Tool Category | Description |
|---------------|-------------|
| `filesystem` | File read / write / edit / delete |
| `memory` | Memory read / search |
| `providers` | Service provider API calls |
| `runtime` | Command execution, process management |
| `sessions` | Session management operations |
| `ui` | UI interaction helpers |
| `web` | Web search and content fetching |

---

## 8. Providers Integration

A Provider is an external integration package connecting the Agent to an
enterprise system. AtlasClaw core loads providers from `providers_root`; the
provider repository owns concrete provider behavior and examples.

### 8.1 Provider Structure

```
<providers_root>/<provider-name>/
├── PROVIDER.md
├── provider.schema.json
├── README.md
├── assets/
└── skills/
    └── <skill-name>/
        ├── SKILL.md
        └── scripts/         # optional
```

### 8.2 Core / Provider Boundary

Core documentation covers:

- loading providers from `providers_root`
- system-level skills and channels owned by core
- provider metadata, manifest, and configuration contracts
- runtime behavior such as instance selection and permission propagation

Provider repositories cover:

- target-system auth models
- provider-specific fields and workflow semantics
- provider-specific UI copy, webhook examples, and operator guidance

`provider.schema.json` is the machine-readable provider manifest consumed by
runtime/API/UI code and code-analysis agents. `PROVIDER.md` is the natural
language provider contract included in LLM context. `README.md` is package
documentation for humans. Runtime must never parse UI/config schema from
`PROVIDER.md` body tables.

### 8.3 Adding A Custom Provider

1. Create the package under `providers_root`
2. Add `PROVIDER.md`, `provider.schema.json`, and provider-owned skills
3. Register instances under `service_providers`
4. Restart or reload AtlasClaw so the package is discovered

See [PROVIDER-GUIDE.MD](./PROVIDER-GUIDE.MD) for the complete guide.

---

## 9. Authentication

Configured under the `auth` section in `atlasclaw.json`. The following providers are supported:

### 9.1 No Authentication (development / testing)

```json
{
  "auth": {
    "provider": "none",
    "none": {
      "default_user_id": "default"
    }
  }
}
```

### 9.2 API Key Authentication

```json
{
  "auth": {
    "provider": "api_key",
    "cache_ttl_seconds": 300,
    "api_key": {
      "keys": {
        "sk-your-key-001": { "user_id": "user1", "roles": ["admin"] },
        "sk-your-key-002": { "user_id": "user2", "roles": ["user"] }
      }
    }
  }
}
```

Include in the request header: `Authorization: Bearer sk-your-key-001`

### 9.3 OIDC Authentication

Used for **API token** validation (M2M / service-to-service). The token is passed via `Authorization: Bearer <token>` and validated against the IdP's JWKS endpoint.

```json
{
  "auth": {
    "provider": "oidc",
    "oidc": {
      "issuer": "https://auth.example.com",
      "client_id": "${OIDC_CLIENT_ID}",
      "client_secret": "${OIDC_CLIENT_SECRET}",
      "scopes": ["openid", "profile", "email"]
    }
  }
}
```

### 9.4 OAuth2 SSO Authentication (Browser Login)

Used for **browser-based interactive login** via the standard OAuth2 Authorization Code Flow (with PKCE). After login, a signed session cookie is issued — no manual token management needed.

Supported IdPs: Keycloak, Okta, Azure AD, Auth0, Dex, or any OIDC-compliant IdP.

#### Configuration

```json
{
  "auth": {
    "provider": "sso",
    "sso": {
      "issuer": "https://sso.example.com/realms/your-realm",
      "client_id": "${SSO_CLIENT_ID}",
      "client_secret": "${SSO_CLIENT_SECRET}",
      "redirect_base_url": "https://atlasclaw.example.com",
      "post_login_redirect": "/",
      "post_logout_redirect": "/",
      "scopes": ["openid", "profile", "email"],
      "session_secret": "${SSO_SESSION_SECRET}",
      "session_ttl_seconds": 28800,
      "use_pkce": true,
      "verify_ssl": true
    }
  }
}
```

#### SSO Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `issuer` | Yes | — | IdP issuer URL; OIDC Discovery performed automatically |
| `client_id` | Yes | — | OAuth2 client ID registered in the IdP |
| `client_secret` | Yes | — | OAuth2 client secret |
| `redirect_base_url` | Yes | — | Public base URL of this AtlasClaw instance, used to build the callback URL |
| `scopes` | No | `["openid","profile","email"]` | OAuth2 scopes |
| `session_secret` | No | auto-generated | HMAC-SHA256 signing key; if omitted, sessions are invalidated on restart |
| `session_ttl_seconds` | No | `28800` (8 h) | Session validity |
| `use_pkce` | No | `true` | Enable PKCE (RFC 7636) |
| `verify_ssl` | No | `true` | Verify IdP TLS certificate |

#### SSO Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/login` | GET | Initiate SSO — redirects to IdP |
| `/auth/callback` | GET | OAuth2 callback — exchanges code for session cookie |
| `/auth/logout` | GET / POST | Log out — clears session, optionally does IdP RP-initiated logout |
| `/auth/me` | GET | Returns current user info (requires valid session) |
| `/auth/status` | GET | `{ "authenticated": true/false }` — no auth required |

#### Login Flow

```
Browser                    AtlasClaw                  IdP
   │  GET /auth/login         │                         │
   │─────────────────────────>│                         │
   │<─ 302 + state cookie ────│                         │
   │  GET /authorize?...      │                         │
   │─────────────────────────────────────────────────>  │
   │  (user enters credentials)                         │
   │<─────────────────────────────────────────────────  │
   │  GET /auth/callback?code=│                         │
   │─────────────────────────>│── POST /token ─────────>│
   │                          │<─ tokens ───────────────│
   │                          │── GET /userinfo ────────>│
   │                          │<─ profile ──────────────│
   │<─ 302 + Set-Cookie: atlasclaw_sso_session=... ─────│
   │  (subsequent requests carry session cookie)        │
```

#### Frontend SSO Helpers

```javascript
// Check authentication status
const { authenticated } = await fetch('/auth/status').then(r => r.json());
if (!authenticated) {
  window.location.href = `/auth/login?next=${encodeURIComponent(window.location.pathname)}`;
}

// Get current user
const user = await fetch('/auth/me').then(r => r.json());
// { user_id, display_name, email, roles, ... }

// Logout
window.location.href = '/auth/logout';
```

#### IdP-Specific Examples

**Keycloak**
```json
{
  "sso": {
    "issuer": "https://keycloak.example.com/realms/my-realm",
    "client_id": "atlasclaw",
    "client_secret": "${KEYCLOAK_CLIENT_SECRET}",
    "redirect_base_url": "https://atlasclaw.example.com"
  }
}
```

**Okta**
```json
{
  "sso": {
    "issuer": "https://your-org.okta.com/oauth2/default",
    "client_id": "${OKTA_CLIENT_ID}",
    "client_secret": "${OKTA_CLIENT_SECRET}",
    "redirect_base_url": "https://atlasclaw.example.com"
  }
}
```

**Azure AD**
```json
{
  "sso": {
    "issuer": "https://login.microsoftonline.com/{tenant-id}/v2.0",
    "client_id": "${AZURE_CLIENT_ID}",
    "client_secret": "${AZURE_CLIENT_SECRET}",
    "scopes": ["openid", "profile", "email", "offline_access"],
    "redirect_base_url": "https://atlasclaw.example.com"
  }
}
```

> Register `https://atlasclaw.example.com/auth/callback` as an allowed redirect URI in your IdP console.

---

## 10. Advanced Configuration

All options below are optional and have sensible defaults.

### 10.1 Agent Behavior

```json
{
  "agent_defaults": {
    "timeout_seconds": 600,
    "max_concurrent": 4,
    "max_tool_calls": 50,
    "prompt_mode": "full"
  }
}
```

`prompt_mode` options:

| Mode | Description |
|------|-------------|
| `full` | Complete runtime prompt — identity, tools, security, skills, workspace, docs |
| `minimal` | Sub-agent mode without optional runtime parts |
| `none` | Basic identity line only |

### 10.2 Message Queue

```json
{
  "messages": {
    "queue": {
      "mode": "collect",
      "debounce_ms": 1000,
      "cap": 20,
      "drop": "old"
    }
  }
}
```

| Queue Mode | Description |
|------------|-------------|
| `collect` | Collect messages and process together (default) |
| `steer` | Immediately interrupt current execution with new message |
| `followup` | Append as a follow-up task after current run |
| `interrupt` | Force interrupt |

### 10.3 Context Compaction

Automatically compresses long conversation history to stay within context window:

```json
{
  "compaction": {
    "reserve_tokens_floor": 20000,
    "soft_threshold_tokens": 4000,
    "context_window": 128000,
    "memory_flush_enabled": true
  }
}
```

### 10.4 Session Reset Policy

```json
{
  "reset": {
    "mode": "daily",
    "daily_hour": 4,
    "idle_minutes": 60
  }
}
```

| Mode | Description |
|------|-------------|
| `daily` | Reset at a specified hour each day |
| `idle` | Reset after idle for the specified number of minutes |
| `manual` | Reset only when triggered via API |

### 10.5 Security Policy

```json
{
  "security": {
    "allowed_tools": [],
    "denied_tools": ["exec_command"],
    "workspace_access": "rw"
  }
}
```

---

## 11. Development & Testing

### 11.1 Running Tests

```bash
# Run all tests
pytest tests/atlasclaw -q

# Run a single test file
pytest tests/atlasclaw/test_agent.py -v

# LLM integration tests (requires a real API key)
pytest tests/atlasclaw/test_agent_integration.py -v -m llm

# End-to-end tests (requires a running service)
pytest tests/atlasclaw/test_e2e_api.py -v -m e2e

# With coverage report
pytest --cov=app.atlasclaw --cov-report=term-missing

# Skip slow tests
pytest -m "not slow"
```

### 11.2 Test Markers

| Marker | Description |
|--------|-------------|
| `slow` | Tests > 1 second |
| `integration` | Integration tests (external dependencies) |
| `e2e` | End-to-end tests (requires running service) |
| `llm` | Tests requiring a real LLM API key |

### 11.3 Frontend Tests

```bash
cd app/frontend
npm install
npm test
```

### 11.4 Debug Mode

```bash
export LOG_LEVEL=DEBUG
uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000 --reload
```

### 11.5 Adding a Custom Skill

1. Create `skills/<skill-name>/` in the project root
2. Add `SKILL.md` with frontmatter metadata and execution instructions
3. (Optional) Add `scripts/handler.py` for executable logic
4. Restart the service — the Skill auto-registers

See [SKILL-GUIDE.MD](./SKILL-GUIDE.MD) for the complete guide.

### 11.6 Verify Skill Loading

```bash
# List all registered skills
curl http://localhost:8000/api/skills

# Test a skill directly
curl -X POST http://localhost:8000/api/skills/execute \
  -H "Content-Type: application/json" \
  -d '{"skill_name": "my-skill", "args": {}}'
```

---

## 12. Architecture Reference

### Repository Layout

```
AtlasClaw-Core/
├── app/
│   ├── frontend/               # Web UI (DeepChat + custom JS)
│   │   ├── index.html
│   │   ├── scripts/            # JS modules (api-client, session-manager, etc.)
│   │   ├── styles/
│   │   ├── locales/            # i18n (zh-CN.json, en-US.json)
│   │   ├── static/             # Static assets (DeepChat bundle)
│   │   └── config.json         # Runtime frontend config
│   └── atlasclaw/              # Core backend
│       ├── main.py             # FastAPI application entry
│       ├── agent/              # Agent engine (runner, routing, prompt builder, streaming)
│       ├── api/                # REST / WebSocket / SSE endpoints
│       ├── auth/               # Authentication (middleware, strategy, providers)
│       ├── channels/           # Channel adapters (REST, SSE, WebSocket)
│       ├── core/               # Config, dependency injection, provider registry
│       ├── memory/             # Long-term memory (vector + full-text)
│       ├── session/            # Session persistence and management
│       ├── skills/             # Skill loading and registry
│       ├── tools/              # Built-in tool suite
│       └── workflow/           # Workflow engine
├── tests/                      # Test suite
│   ├── atlasclaw/              # Python tests
│   └── frontend/               # JavaScript tests
├── docs/                       # Documentation
├── atlasclaw.json              # Main configuration (create from .example)
├── atlasclaw.json.example      # Configuration template
└── requirements.txt
```

### Data Flow

```
User Request
    ↓
API Layer (routes.py)
    ↓
Auth Middleware
    ↓
Agent Runner  ←── Session Manager
    ↓                  ↓
Skill Execution    Memory Manager
    ↓
Provider API
    ↓
Response Stream (SSE)
```

### Session & Memory Storage

```
~/.atlasclaw/
├── agents/
│   └── <agent_id>/
│       ├── sessions/
│       │   └── <user_id>/
│       │       ├── sessions.json
│       │       ├── <session_id>.jsonl
│       │       └── archive/
│       └── memory/
│           ├── vector/
│           └── fulltext/
└── config.json
```

### Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent Framework | PydanticAI | Type-safe, structured output, native tool calling |
| Web Framework | FastAPI | Async-native, auto API docs, type hints |
| Configuration | Pydantic + layered loading | Type-safe, flexible overrides |
| Authentication | Strategy pattern + shadow users | Multiple auth sources, unified user model |
| Storage | File system (JSON / JSONL / Markdown) | Simple, version-controllable, debuggable |
| Streaming | SSE | Simpler than WebSocket, supports auto-reconnect |
| Skills System | Markdown + Python hybrid | Flexible: docs-only or executable |

### Further Reading

| Document | Purpose |
|----------|---------|
| [DEPLOYMENT.MD](./DEPLOYMENT.MD) | Production deployment, Docker, systemd, Nginx |
| [SKILL-GUIDE.MD](./SKILL-GUIDE.MD) | Complete Skill development reference |
| [PROVIDER-GUIDE.MD](./PROVIDER-GUIDE.MD) | Provider development and deployment |
| [FILE-STRUCTURE.MD](./FILE-STRUCTURE.MD) | Detailed module map and import patterns |
| [DEVELOPMENT-SPEC.MD](./DEVELOPMENT-SPEC.MD) | Python style guide and testing standards |
| [PROJECT_OVERVIEW.MD](./PROJECT_OVERVIEW.MD) | Architecture deep-dive and design decisions |
