# AtlasClaw Development Specification

> **Canonical development standards for the AtlasClaw enterprise agent framework.**
> All future development and feature changes MUST follow these specifications.
>
> This document consolidates content previously in `CODE_STRUCTURE.md`, `CODING_STANDARDS.md`,
> and `DEPLOYMENT.md`. Those files are no longer maintained separately.
>
> Related documents:
>
> - [Architecture](./ARCHITECTURE.MD) — system design, bootstrap, request lifecycle
> - [Module Details](./MODULE-DETAILS.MD) — per-module API surface and internals

***

## Table of Contents

1. [General Principles](#1-general-principles)
2. [Project Structure](#2-project-structure)
3. [Code Style](#3-code-style)
4. [Architecture Patterns](#4-architecture-patterns)
5. [Error Handling](#5-error-handling)
6. [Security Requirements](#6-security-requirements)
7. [Async Patterns](#7-async-patterns)
8. [Testing Standards](#8-testing-standards)
9. [Extension Development](#9-extension-development)
10. [Configuration Standards](#10-configuration-standards)
11. [Tools & Automation](#11-tools-automation)
12. [Deployment & Operations](#12-deployment-operations)
13. [Git & Commit Standards](#13-git-commit-standards)
14. [Code Review Checklist](#14-code-review-checklist)

***

## 1. General Principles

### 1.1 Readability First

Code is read more often than it is written. Prioritize clarity over cleverness.

```python
# Good
async def get_user_by_id(user_id: str) -> User | None:
    """Retrieve a user by their unique identifier."""
    return await user_repository.find_by_id(user_id)

# Bad
async def g(u: str) -> Any:
    return await ur.f(u)
```

### 1.2 Explicit Over Implicit

Be explicit about behavior, types, and intentions.

```python
# Good
def process_data(data: dict[str, Any]) -> ProcessedResult:
    if not data:
        raise ValueError("Data cannot be empty")
    return ProcessedResult.from_dict(data)

# Bad
def process_data(data):
    return ProcessedResult(data)
```

### 1.3 Single Responsibility

Each function, class, and module should have one clear purpose.

***

## 2. Project Structure

> Also see [Architecture](./ARCHITECTURE.MD) §3 for a high-level directory overview.

### 2.1 Directory Tree

```
AtlasClaw-Core/
├── app/
│   ├── frontend/                    # Web UI (DeepChat-based)
│   │   ├── index.html              # Main entry point
│   │   ├── scripts/                # JavaScript modules
│   │   │   ├── api-client.js       # API client
│   │   │   ├── session-manager.js  # Session management
│   │   │   ├── message-handler.js  # Message handling
│   │   │   └── ui-components.js    # UI components
│   │   ├── styles/                 # CSS styles
│   │   ├── locales/                # i18n translations
│   │   └── static/                 # Static assets
│   │
│   └── atlasclaw/                    # Core backend
│       ├── __init__.py
│       ├── main.py                 # FastAPI application entry
│       │
│       ├── agent/                  # Agent Engine
│       │   ├── __init__.py
│       │   ├── runner.py           # Agent execution engine
│       │   ├── routing.py          # Multi-agent routing
│       │   ├── prompt_builder.py   # System prompt builder
│       │   ├── stream.py           # Streaming events & chunking
│       │   ├── compaction.py       # Context compaction
│       │   ├── token_policy.py     # Token selection strategies
│       │   └── agent_pool.py       # Agent instance caching
│       │
│       ├── api/                    # API Layer
│       │   ├── __init__.py
│       │   ├── routes.py           # REST API routes
│       │   ├── gateway.py          # WebSocket gateway
│       │   ├── sse.py              # SSE manager
│       │   ├── websocket.py        # WebSocket handler
│       │   └── request_orchestrator.py
│       │
│       ├── auth/                   # Authentication System
│       │   ├── __init__.py
│       │   ├── middleware.py       # Auth middleware
│       │   ├── strategy.py         # Auth strategy
│       │   ├── models.py           # User models
│       │   ├── config.py           # Auth configuration
│       │   ├── shadow_store.py     # Shadow user store
│       │   └── providers/          # Auth providers
│       │       ├── __init__.py
│       │       ├── base.py
│       │       ├── none.py
│       │       ├── api_key.py
│       │       ├── oidc.py
│       │       └── smartcmp.py
│       │
│       ├── channels/               # Channel Adapters
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── registry.py
│       │   ├── rest_adapter.py
│       │   ├── sse_adapter.py
│       │   └── websocket_adapter.py
│       │
│       ├── core/                   # Core Infrastructure
│       │   ├── __init__.py
│       │   ├── config.py           # Configuration manager
│       │   ├── config_schema.py    # Pydantic config schemas
│       │   ├── deps.py             # Skill dependency injection
│       │   ├── execution_context.py
│       │   ├── provider_registry.py
│       │   ├── tenant.py
│       │   ├── token_pool.py       # Multi-token management
│       │   ├── token_health_store.py # Token health persistence
│       │   └── token_interceptor.py  # Rate-limit header extraction
│       │
│       ├── hooks/                  # Hook System
│       │   └── system.py
│       │
│       ├── media/                  # Media Processing
│       │   ├── link_extractor.py
│       │   ├── tts.py
│       │   └── understanding.py
│       │
│       ├── memory/                 # Memory System
│       │   ├── __init__.py
│       │   ├── manager.py
│       │   └── search.py
│       │
│       ├── messages/               # Message Processing
│       │   ├── command.py
│       │   └── handler.py
│       │
│       ├── models/                 # Model Management
│       │   ├── __init__.py
│       │   ├── providers.py
│       │   ├── retry.py
│       │   └── failover.py
│       │
│       ├── providers/              # Built-in Providers
│       │   └── jira/               # Jira Provider Example
│       │       ├── skills/
│       │       │   └── jira-issue/
│       │       │       ├── SKILL.md
│       │       │       └── scripts/
│       │       └── scripts/
│       │
│       ├── session/                # Session Management
│       │   ├── __init__.py
│       │   ├── manager.py
│       │   ├── context.py
│       │   └── queue.py
│       │
│       ├── skills/                 # Skills System
│       │   ├── __init__.py
│       │   ├── registry.py
│       │   └── frontmatter.py
│       │
│       ├── tools/                  # Built-in Tools
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── catalog.py
│       │   ├── registration.py
│       │   ├── filesystem/         # File system tools
│       │   ├── memory/             # Memory tools
│       │   ├── providers/          # Provider tools
│       │   ├── runtime/            # Runtime tools
│       │   ├── sessions/           # Session tools
│       │   ├── ui/                 # UI tools
│       │   └── web/                # Web tools
│       │
│       └── workflow/               # Workflow Engine
│           ├── __init__.py
│           ├── engine.py
│           └── orchestrator.py
│
├── tests/                          # Test Suite
│   ├── conftest.py                 # pytest configuration
│   ├── atlasclaw.test.json           # Test configuration
│   ├── atlasclaw/                    # Python tests
│   │   ├── test_core.py
│   │   ├── test_agent.py
│   │   ├── test_agent_integration.py
│   │   ├── test_e2e_api.py
│   │   ├── auth/
│   │   ├── memory/
│   │   ├── session/
│   │   └── providers/
│   └── frontend/                   # Frontend tests
│
├── docs/                           # Documentation
│   ├── architecture.md
│   ├── module-details.md
│   ├── development-spec.md
│   └── images/architecture/
│
├── openspec/                       # OpenSpec Specifications
│   ├── AGENTS.md
│   ├── project.md
│   ├── changes/
│   └── completed/
│
├── atlasclaw.json                    # Main configuration
└── requirements.txt                # Python dependencies
```

### 2.2 Module Dependencies

```
main.py
├── agent/runner.py
│   ├── core/deps.py
│   ├── session/manager.py
│   └── skills/registry.py
├── api/routes.py
│   ├── agent/runner.py
│   ├── auth/middleware.py
│   └── session/manager.py
├── auth/middleware.py
│   └── auth/strategy.py
├── core/config.py
│   └── core/config_schema.py
└── skills/registry.py
    └── tools/registration.py
```

### 2.3 Data Flow

```
User Request
    ↓
API Layer (routes.py)
    ↓
Auth Middleware
    ↓
Agent Runner
    ↓
Skill Execution
    ↓
Provider API
    ↓
Response Stream (SSE)
```

### 2.4 Key Files

**Entry Points**:

| File                      | Purpose                   |
| ------------------------- | ------------------------- |
| `app/atlasclaw/main.py`   | FastAPI application entry |
| `app/frontend/index.html` | Web UI entry              |

**Configuration**:

| File                                  | Purpose                 |
| ------------------------------------- | ----------------------- |
| `atlasclaw.json`                      | Main configuration file |
| `tests/atlasclaw.test.json`           | Test configuration      |
| `app/atlasclaw/core/config_schema.py` | Configuration schemas   |

**Core Logic**:

| File                               | Purpose                       |
| ---------------------------------- | ----------------------------- |
| `app/atlasclaw/agent/runner.py`    | Agent execution engine        |
| `app/atlasclaw/agent/token_policy.py` | Token selection strategies  |
| `app/atlasclaw/agent/agent_pool.py` | Agent instance caching        |
| `app/atlasclaw/core/token_pool.py` | Multi-token management        |
| `app/atlasclaw/core/token_health_store.py` | Token health persistence |
| `app/atlasclaw/skills/registry.py` | Skills registration           |
| `app/atlasclaw/session/manager.py` | Session management            |
| `app/atlasclaw/memory/manager.py`  | Memory management             |

**API**:

| File                             | Purpose           |
| -------------------------------- | ----------------- |
| `app/atlasclaw/api/routes.py`    | REST API routes   |
| `app/atlasclaw/api/sse.py`       | SSE streaming     |
| `app/atlasclaw/api/websocket.py` | WebSocket handler |

### 2.5 File Naming Conventions

| Type         | Pattern            | Example           |
| ------------ | ------------------ | ----------------- |
| Modules      | `snake_case.py`    | `runner.py`       |
| Classes      | `PascalCase`       | `AgentRunner`     |
| Functions    | `snake_case`       | `run_agent`       |
| Constants    | `UPPER_SNAKE_CASE` | `MAX_RETRY`       |
| Tests        | `test_*.py`        | `test_runner.py`  |
| Test Classes | `Test*`            | `TestAgentRunner` |
| Test Methods | `test_*`           | `test_run`        |

### 2.6 Import Patterns

**Standard pattern**:

```python
# Standard library
import asyncio
from pathlib import Path
from typing import Any

# Third-party
import httpx
from pydantic import BaseModel

# Local application
from app.atlasclaw.core.config import get_config
from app.atlasclaw.session.manager import SessionManager
```

**Relative imports** (within a package):

```python
from .base import BaseTool
from ..core.config import get_config
```

### 2.7 Data Storage Layout

**Session storage**:

```
~/.atlasclaw/agents/<agent_id>/sessions/<user_id>/
├── sessions.json
├── <session_id>.jsonl
└── archive/
```

**Memory storage**:

```
~/.atlasclaw/agents/<agent_id>/memory/
├── vector/
└── fulltext/
```

**Configuration storage**:

```
~/.atlasclaw/
├── config.json
└── workspace/
```

**Token health storage**:

```
<workspace>/token_health.json    # Token rate-limit state
```

***

## 3. Code Style

### 3.1 Python

**Base**: PEP 8 with the following project conventions.

**File Header**:

```python
# -*- coding: utf-8 -*-
"""Module docstring describing purpose."""

from __future__ import annotations
```

**Formatting**:

- Line length: \~100 characters (be reasonable, not dogmatic)
- Indentation: 4 spaces
- String quotes: double quotes (`"`) unless single quotes avoid escaping
- Encoding: UTF-8 with `# -*- coding: utf-8 -*-` header
- Use parentheses for implicit line continuation

```python
# Good
result = await some_long_function_name(
    first_parameter,
    second_parameter,
    third_parameter,
)
```

**Imports** (ordered with blank lines between groups):

```python
# 1. Standard library
import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

# 2. Third-party
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

# 3. Local
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.auth.models import UserInfo
```

- Use absolute imports: `from app.atlasclaw.core.deps import SkillDeps`
- Relative imports allowed within the same package (e.g., `from .models import UserInfo`)
- Use `TYPE_CHECKING` for imports only needed for type hints to avoid circular imports
- Never use wildcard imports (`from module import *`)

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.atlasclaw.agent.runner import AgentRunner
```

**Type Hints**:

- Mandatory on all function parameters and return values
- Use `Optional[T]` for nullable types (preferred over `T | None`)
- Use `@dataclass` for data containers
- Use `class Name(str, Enum)` for string constants
- Use `from __future__ import annotations` for forward references
- Use generic types from `typing` for complex signatures

```python
from typing import TypeVar

T = TypeVar("T")

def get_first(items: list[T]) -> T | None:
    return items[0] if items else None
```

**Naming Conventions**:

| Element              | Convention             | Example                               |
| -------------------- | ---------------------- | ------------------------------------- |
| Functions, variables | `snake_case`           | `resolve_user`, `session_key`         |
| Classes, exceptions  | `PascalCase`           | `AgentRunner`, `AuthenticationError`  |
| Constants            | `SCREAMING_SNAKE_CASE` | `ANONYMOUS_USER`, `GROUP_FS`          |
| Private members      | `_` prefix             | `_hooks`, `_build_prompt`             |
| Modules              | `snake_case`           | `shadow_store.py`, `config_schema.py` |

Use verb-noun format for functions:

```python
# Good
def fetch_user_data(): ...
def validate_input(): ...

# Bad
def user_data(): ...
def validation(): ...
```

**Documentation**:

- Docstrings on all public classes and methods
- Module docstrings on every module
- Triple quotes on separate lines
- Google-style format with `Args:`, `Returns:`, `Raises:` sections
- Comments in both English and Chinese are acceptable
- Inline comments should explain WHY, not WHAT

```python
"""Session management module.

This module provides session lifecycle management including creation,
persistence, retrieval, and cleanup of user sessions.

Key components:
    SessionManager: Main class for session operations
    SessionContext: Context manager for session state
"""
```

```python
def get_or_create(self, provider: str, result: AuthResult) -> ShadowUser:
    """Look up or create a shadow user for the given external identity.

    Args:
        provider: Auth provider name (e.g., "oidc", "smartcmp").
        result: Authentication result from the provider.

    Returns:
        The existing or newly created shadow user.

    Raises:
        IOError: If the user store file cannot be written.
    """
```

```python
# Good: Explains WHY
# Compensate for edge case where timezone offset affects date calculation
adjusted_date = raw_date + timedelta(hours=timezone_offset)

# Bad: Restates the obvious
# Add one to count
count += 1
```

### 3.2 JavaScript (Frontend)

- ES modules with `"type": "module"` in package.json
- 2-space indentation
- Single quotes for strings
- No semicolons (be consistent with existing code)
- Use `const` by default, `let` when reassignment is needed

***

## 4. Architecture Patterns

### 4.1 Thin Core / Rich Providers

The core framework handles routing, lifecycle, and coordination. Domain-specific logic goes in providers.

**When to put logic in core**:

- Request routing and session management
- Configuration loading and merging
- Authentication pipeline orchestration
- Streaming event emission
- Tool/skill registration infrastructure

**When to put logic in a provider**:

- External system API calls
- Business domain workflows
- Custom data transformations
- Platform-specific message formatting

### 4.2 Request-Scoped Dependency Injection

All request context flows through `SkillDeps` via `RunContext[SkillDeps]`.

```python
@agent.tool
async def my_tool(ctx: RunContext[SkillDeps], query: str) -> str:
    deps = ctx.deps
    # Access user identity
    user = deps.user_info
    # Access upstream token for API calls
    headers = {"Authorization": f"Bearer {deps.user_token}"}
    # Check abort signal
    if deps.is_aborted():
        return "Aborted"
```

**What belongs in SkillDeps**: User identity, session key, channel, abort signal, session/memory managers, HTTP clients for upstream services.

**What does NOT belong in SkillDeps**: Business logic, computed results, temporary state, module-specific helpers.

### 4.3 Registry Pattern

Used by `ChannelRegistry`, `AuthRegistry`, and `SkillRegistry`. All follow the same convention:

```python
class XxxRegistry:
    _registry: dict[str, Type[XxxHandler]] = {}

    @classmethod
    def register(cls, type_id: str, handler_class: Type[XxxHandler]) -> None: ...

    @classmethod
    def get(cls, type_id: str) -> Optional[Type[XxxHandler]]: ...

    @classmethod
    def list_handlers(cls) -> dict[str, Type[XxxHandler]]: ...
```

Registries are class-level (not instance-level) and populated at startup.

### 4.4 Strategy Pattern

`AuthStrategy` is the canonical example: it delegates to a pluggable `AuthProvider` while managing caching and shadow user resolution.

Use when: An operation needs multiple interchangeable implementations selected at configuration time.

### 4.5 Adapter Pattern

`ChannelHandler` normalizes external protocols (Feishu, DingTalk, WebSocket) into a common `InboundMessage`/`OutboundMessage` format.

Use when: Integrating with external systems that have different protocols but serve the same logical purpose.

### 4.6 Hook Pattern

The `HookSystem` provides cross-cutting concern injection at 17+ lifecycle phases.

**When to use SEQUENTIAL**: The hook needs to modify the context (e.g., add/remove fields, transform data).

**When to use PARALLEL**: The hook is observe-only (e.g., logging, metrics, notifications).

**Priority conventions**: Lower values run first. Use `50` for core hooks, `100` (default) for standard hooks, `999` for logging/observability hooks.

***

## 5. Error Handling

### 5.1 Exception Hierarchy

Define custom exceptions for your module:

```python
class AtlasClawError(Exception):
    """Base exception for all AtlasClaw errors."""
    pass

class SessionError(AtlasClawError):
    """Exception raised for session-related errors."""
    pass

class SessionNotFoundError(SessionError):
    """Exception raised when a session cannot be found."""
    pass
```

### 5.2 Exception Types

- Use specific exception types, never bare `except:`
- Include error context in exception messages

```python
# Correct
try:
    user = await fetch_user(user_id)
except UserNotFoundError:
    logger.warning("User not found: %s", user_id)
    return None
except NetworkError as e:
    logger.error("Network error fetching user: %s", e)
    raise

# Wrong
try:
    user = await fetch_user(user_id)
except Exception:
    return None
```

### 5.3 Result Objects for Expected Failures

Use result objects when failure is a normal outcome, not an exceptional condition:

```python
@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None

# Usage
result = await channel.send_message(msg)
if not result.success:
    logger.warning(f"Send failed: {result.error}")
```

### 5.4 Context Managers

Use context managers for resource management:

```python
# Good
async with httpx.AsyncClient() as client:
    response = await client.get(url)
    return response.json()

# Bad
client = httpx.AsyncClient()
response = await client.get(url)
return response.json()  # Client never closed!
```

### 5.5 Cancellation Signals

Use `asyncio.Event` for abort/cancellation:

```python
# Check periodically in long-running operations
if deps.is_aborted():
    return StreamEvent.lifecycle_aborted()

# Signal abort from outside
deps.abort()
```

### 5.6 Error Logging

- Log errors with sufficient context (session key, user ID, operation)
- Use `print()` for hook system errors (current pattern)
- Include the original exception when re-raising

***

## 6. Security Requirements

### 6.1 Permission Inheritance

**Rule**: The agent NEVER creates its own authorization layer. Every operation inherits the authenticated user's permissions from the upstream system.

```python
# Correct: Pass user's token to upstream API
headers = {"Authorization": f"Bearer {deps.user_token}"}
resp = await client.get("/api/resource", headers=headers)

# Wrong: Check permissions internally
if user.has_role("admin"):  # Never do this
    ...
```

### 6.2 Token Propagation

- `SkillDeps.user_info.raw_token` is always available to skills and tools
- Always use `deps.user_token` (or `deps.user_info.raw_token`) for upstream API calls
- Never cache tokens beyond the auth strategy's TTL

### 6.3 Tool Filtering

Use `ToolPolicy` to restrict tool access per agent:

- `allow: ["*"]` — All tools (default)
- `deny: ["exec", "process"]` — Block specific tools
- Deny takes priority over allow
- Supports wildcards: `sessions_*`, `*_search`

### 6.4 Tenant Isolation

- Session keys, memory paths, and storage paths are prefixed with `tenant_id`
- Use `TenantIsolation` static methods for path construction
- Never allow cross-tenant data access

### 6.5 Secrets Management

- Never hardcode secrets in source code
- Use `${VAR_NAME}` or `${VAR_NAME:default}` interpolation in config files
- API keys in config are masked in `get_instance_config_redacted()` output
- Sensitive fields: `api_key`, `client_secret`, `cookie`, `password`, `token`

```python
# Good
api_key = os.environ.get("API_KEY")
if not api_key:
    raise ValueError("API_KEY environment variable not set")

# Bad
api_key = "sk-1234567890abcdef"  # Never do this!
```

### 6.6 Input Validation

Always validate external input:

```python
# Good
def process_user_input(data: dict[str, Any]) -> None:
    if not isinstance(data.get("user_id"), str):
        raise ValueError("user_id must be a string")
    if len(data["user_id"]) > 100:
        raise ValueError("user_id too long")
```

### 6.7 Production Security Best Practices

1. Never commit secrets to version control
2. Use environment variables for sensitive data
3. Enable HTTPS in production
4. Set up firewall rules to restrict access
5. Regularly update dependencies
6. Use strong authentication for admin access
7. Monitor logs for suspicious activity

***

## 7. Async Patterns

### 7.1 Core Rule

All I/O-bound operations MUST be async. This includes file reads/writes, HTTP calls, subprocess execution, and database queries.

```python
# Good
async def fetch_data(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()

# Bad (blocking I/O in async function)
async def fetch_data(url: str) -> dict[str, Any]:
    response = requests.get(url)  # Blocking!
    return response.json()
```

### 7.2 Coordination

Use `asyncio.Event` for cancellation/abort signals:

```python
abort_signal = asyncio.Event()

# In the runner
if abort_signal.is_set():
    yield StreamEvent.lifecycle_aborted()
    return

# From outside
abort_signal.set()
```

### 7.3 Concurrency Control

Use per-session `asyncio.Lock` to prevent concurrent transcript writes:

```python
# SessionManager uses per-session locks internally
async with self._locks[session_key]:
    await self._append_to_file(path, entry)
```

### 7.4 Concurrent Execution

Use `asyncio.gather()` for concurrent operations:

```python
async def fetch_multiple(urls: list[str]) -> list[dict[str, Any]]:
    tasks = [fetch_data(url) for url in urls]
    return await asyncio.gather(*tasks)
```

### 7.5 Async Testing

Always use `@pytest.mark.asyncio` for async tests:

```python
@pytest.mark.asyncio
async def test_resolve_user():
    strategy = AuthStrategy(provider=mock_provider, shadow_store=store)
    user = await strategy.resolve_user("token-abc")
    assert user.user_id != "anonymous"
```

***

## 8. Testing Standards

### 8.1 File Structure

| Convention   | Pattern              | Example                       |
| ------------ | -------------------- | ----------------------------- |
| Test files   | `test_<module>.py`   | `test_agent.py`               |
| Test classes | `Test<PascalCase>`   | `TestStreamEvent`             |
| Test methods | `test_<description>` | `test_create_lifecycle_start` |
| Fixtures     | `conftest.py`        | Shared fixtures at test root  |

### 8.2 Test Organization

Use Arrange-Act-Assert pattern:

```python
class TestUserService:
    """Test suite for UserService."""

    @pytest.fixture
    def user_service(self):
        """Create a UserService instance for testing."""
        return UserService()

    async def test_get_user_returns_user_when_exists(self, user_service):
        """Test that get_user returns the user when it exists."""
        # Arrange
        user_id = "test-user-123"
        expected_user = User(id=user_id, name="Test User")

        # Act
        result = await user_service.get_user(user_id)

        # Assert
        assert result == expected_user
```

### 8.3 Test Markers

| Marker                     | Usage                                            |
| -------------------------- | ------------------------------------------------ |
| `@pytest.mark.slow`        | Tests taking > 1 second                          |
| `@pytest.mark.integration` | Integration tests requiring running services     |
| `@pytest.mark.e2e`         | End-to-end tests requiring full stack            |
| `@pytest.mark.llm`         | Tests requiring LLM API calls                    |
| `@pytest.mark.asyncio`     | Async tests (required for all `async def` tests) |

### 8.4 Fixture Guidelines

- Use `scope="session"` for expensive resources (e.g., database connections)
- Use `scope="function"` (default) for test isolation
- Clean up in fixture teardown or use `yield`
- Place shared fixtures in `conftest.py`

### 8.5 Mocking

Use `unittest.mock` for mocking:

```python
from unittest.mock import AsyncMock, patch

async def test_fetch_data_with_mock():
    with patch("app.module.client.get") as mock_get:
        mock_get.return_value = AsyncMock(json=lambda: {"data": "test"})
        result = await fetch_data()
        assert result == {"data": "test"}
```

### 8.6 Testing Patterns

```python
# Use fixtures for shared resources
@pytest.fixture
def mock_config():
    return AtlasClawConfig(log_level=LogLevel.DEBUG)

# Use parametrize for data-driven tests
@pytest.mark.parametrize("scope,expected", [
    (SessionScope.MAIN, "agent:main:user:default:main"),
    (SessionScope.PER_PEER, "agent:main:user:default:dm:default"),
])
def test_session_key_format(scope, expected):
    key = SessionKey().to_string(scope)
    assert key == expected
```

### 8.7 Frontend Testing

- Framework: Jest with jsdom environment
- Tests in `tests/frontend/**/*.test.js`
- Run with `npm test` from `app/frontend/`

### 8.8 Running Tests

```bash
# All backend tests
pytest tests/atlasclaw -q

# Single file
pytest tests/atlasclaw/test_agent.py -v

# Single class
pytest tests/atlasclaw/test_agent.py::TestStreamEvent -v

# Single method
pytest tests/atlasclaw/test_agent.py::TestStreamEvent::test_create_lifecycle_start -v

# With coverage
pytest --cov=app.atlasclaw --cov-report=term-missing

# Skip slow tests
pytest -m "not slow"

# Frontend tests
cd app/frontend && npm test
```

***

## 9. Extension Development

### 9.1 Adding a Provider

**Directory structure**:

```
<providers_root>/<provider_name>/    # Default: ../providers/<provider_name>/
├── PROVIDER.md       # Required: documentation + LLM context
└── skills/
    ├── skill-one/
    │   ├── SKILL.md  # Required: skill documentation
    │   └── handler.py  # Optional: executable skill
    └── skill-two/
        └── SKILL.md  # Markdown-only skill
```

**Configuration roots** (in `atlasclaw.json`):

| Config Field | Default | Description |
|-------------|---------|-------------|
| `providers_root` | `../atlasclaw-providers/providers` | Enterprise integrations |
| `skills_root` | `../atlasclaw-providers/skills` | Third-party standalone skills |
| `channels_root` | `../atlasclaw-providers/channels` | System-level channel configs |

All paths are resolved relative to the `atlasclaw.json` file location.

**PROVIDER.md frontmatter**:

```yaml
---
name: my-provider
display_name: My Provider
version: "1.0"
keywords:
  - keyword1
  - keyword2
capabilities:
  - capability description
use_when:
  - "when user wants to..."
avoid_when:
  - "when user is doing..."
---
```

**Instance configuration** (in `atlasclaw.json`):

```json
{
  "service_providers": {
    "my-provider": {
      "production": {
        "base_url": "${MY_PROVIDER_URL}",
        "api_key": "${MY_PROVIDER_KEY}"
      }
    }
  }
}
```

### 9.2 Adding a Skill

**SKILL.md frontmatter**:

```yaml
---
name: my-skill
description: Brief description of what this skill does
keywords:
  - relevant
  - keywords
use_when:
  - "when user needs X"
avoid_when:
  - "when user is doing Y"
parameters:
  param1:
    type: string
    description: Parameter description
    required: true
---
```

**Executable skill handler** (`handler.py`):

```python
from pydantic_ai import RunContext
from app.atlasclaw.core.deps import SkillDeps

async def handler(ctx: RunContext[SkillDeps], param1: str) -> str:
    """Execute the skill logic."""
    deps = ctx.deps
    # Use deps.user_token for upstream API calls
    # Use deps.session_key for session context
    return "Result"
```

### 9.3 Adding a Channel Handler

Implement `ChannelHandler` ABC and register:

```python
from app.atlasclaw.channels.handler import ChannelHandler
from app.atlasclaw.channels.models import ChannelMode

class MyChannelHandler(ChannelHandler):
    channel_type = "my-channel"
    channel_name = "My Channel"
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = True
    supports_webhook = False

    async def setup(self, connection_config) -> bool: ...
    async def start(self, context) -> bool: ...
    async def stop(self) -> bool: ...
    async def connect(self) -> bool: ...
    async def disconnect(self) -> bool: ...

# Register
from app.atlasclaw.channels.registry import ChannelRegistry
ChannelRegistry.register("my-channel", MyChannelHandler)
```

### 9.4 Adding an Auth Provider

Extend `AuthProvider` ABC:

```python
from app.atlasclaw.auth.providers.base import AuthProvider
from app.atlasclaw.auth.models import AuthResult

class MyAuthProvider(AuthProvider):
    @property
    def provider_name(self) -> str:
        return "my-auth"

    async def authenticate(self, credential: str) -> AuthResult:
        # Validate credential against your system
        return AuthResult(
            subject="user-id-from-my-system",
            display_name="User Name",
            roles=["user"],
        )

# Register
from app.atlasclaw.auth.registry import AuthRegistry
AuthRegistry.register("my-auth", MyAuthProvider)
```

### 9.5 Adding a New Tool

1. Add tool definition in `tools/catalog.py` (add to appropriate group or create new group)
2. Implement tool in appropriate subdirectory under `tools/`
3. Register in `tools/registration.py`

***

## 10. Configuration Standards

### 10.1 Adding New Configuration

1. Define a Pydantic model in `core/config_schema.py`:

```python
class MyFeatureConfig(BaseModel):
    """My feature configuration."""
    enabled: bool = False
    max_retries: int = Field(default=3, ge=1, description="Maximum retry attempts")
```

1. Add the field to `AtlasClawConfig`:

```python
class AtlasClawConfig(BaseModel):
    ...
    my_feature: MyFeatureConfig = Field(default_factory=MyFeatureConfig)
```

1. Access in code via `get_config()`:

```python
config = get_config()
if config.my_feature.enabled:
    ...
```

### 10.2 Environment Variable Naming

Pattern: `ATLASCLAW_<SECTION>__<KEY>` (double underscore for nesting)

Examples:

- `ATLASCLAW_LOG_LEVEL=debug`
- `ATLASCLAW_AGENT_DEFAULTS__TIMEOUT_SECONDS=300`
- `ATLASCLAW_MODEL__PRIMARY=openai/gpt-4o`

### 10.3 Secret Interpolation

Use `${VAR_NAME}` or `${VAR_NAME:default_value}` in config files:

```json
{
  "api_key": "${MY_API_KEY}",
  "base_url": "${MY_URL:https://api.example.com}"
}
```

### 10.4 Per-User Configuration

Per-user configuration is stored in `<workspace>/users/<user_id>/user_setting.json`:

```json
{
  "channels": {
    "feishu": {
      "connections": [...]
    }
  },
  "preferences": {
    "language": "zh-CN"
  }
}
```

**Backward compatibility**: The system also checks for legacy `atlasclaw.json` in user directories.

**Note**: User-specific config is separate from the core `atlasclaw.json` (which is workspace-level). Only include fields that differ from the default. Providers are system-level configuration, not user-level.

***

## 11. Tools & Automation

### 11.1 Recommended Tools

| Tool           | Purpose                          |
| -------------- | -------------------------------- |
| **Ruff**       | Fast Python linter and formatter |
| **mypy**       | Static type checker              |
| **pytest**     | Testing framework                |
| **pre-commit** | Git hooks for code quality       |

### 11.2 Pre-commit Configuration

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.7.0
    hooks:
      - id: mypy
```

***

## 12. Deployment & Operations

### 12.1 Prerequisites

**System Requirements**:

| Component | Minimum | Recommended |
| --------- | ------- | ----------- |
| CPU       | 2 cores | 4+ cores    |
| RAM       | 4 GB    | 8+ GB       |
| Disk      | 10 GB   | 50+ GB SSD  |
| Python    | 3.10+   | 3.11+       |

**Required Software**: Python 3.10+, pip or uv, Git

**Optional Software**: Docker and Docker Compose, Nginx (reverse proxy), Redis (caching)

### 12.2 Installation

```bash
# Clone and enter project
git clone <repository-url>
cd AtlasClaw-Core

# Create and activate virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
# Or using uv (faster):
uv pip install -r requirements.txt

# Verify installation
python -c "import app.atlasclaw; print('Installation successful')"
```

### 12.3 Configuration

Create configuration file:

```bash
cp atlasclaw.json.example atlasclaw.json
```

Configure LLM provider in `atlasclaw.json`:

```json
{
  "model": {
    "primary": "deepseek/deepseek-chat",
    "fallbacks": [],
    "temperature": 0.7,
    "selection_strategy": "health",
    "tokens": [
      {
        "id": "token-1",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "${DEEPSEEK_API_KEY_1}",
        "priority": 100,
        "weight": 100
      },
      {
        "id": "token-2",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "${DEEPSEEK_API_KEY_2}",
        "priority": 50,
        "weight": 100
      }
    ],
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

**Token Selection Strategies**:
- `health` — Select by health score (highest first), then priority/weight
- `priority` — Select by priority (highest first), then weight
- `random` — Weighted random selection
- `round_robin` — Rotate through available tokens

Create `.env` file for secrets:

```bash
# LLM API Keys
DEEPSEEK_API_KEY=your-api-key-here

# Optional: Custom config path
ATLASCLAW_CONFIG=/path/to/atlasclaw.json
```

### 12.4 Running the Application

**Development mode**:

```bash
# Set environment variables (Windows PowerShell)
$env:NO_PROXY="*"
$env:ATLASCLAW_CONFIG="atlasclaw.json"

# Run development server
uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000 --reload
```

**Production mode**:

```bash
export NO_PROXY="*"
export ATLASCLAW_CONFIG="atlasclaw.json"

# Run with multiple workers
uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Using Gunicorn**:

```bash
gunicorn app.atlasclaw.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

**Verify deployment**:

```bash
curl http://localhost:8000/api/health
# Expected: {"status": "healthy"}
```

### 12.5 Docker Deployment

**Dockerfile**:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.atlasclaw.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
# Build and run
docker build -t atlasclaw-core .
docker run -d \
  --name atlasclaw \
  -p 8000:8000 \
  -v $(pwd)/atlasclaw.json:/app/atlasclaw.json \
  -e DEEPSEEK_API_KEY=your-api-key \
  atlasclaw-core
```

**Docker Compose**:

```yaml
# docker-compose.yml
version: '3.8'

services:
  atlasclaw:
    build: .
    container_name: atlasclaw-core
    ports:
      - "8000:8000"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
    volumes:
      - ./atlasclaw.json:/app/atlasclaw.json
      - ./data:/app/data
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - atlasclaw
    restart: unless-stopped
```

### 12.6 Production Deployment

**Nginx reverse proxy**:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }

    # SSE endpoint — disable buffering
    location /api/agent/runs/ {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
    }
}
```

**SSL/TLS with Let's Encrypt**:

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

**Systemd service**:

```ini
# /etc/systemd/system/atlasclaw.service
[Unit]
Description=AtlasClaw Core Service
After=network.target

[Service]
Type=simple
User=atlasclaw
Group=atlasclaw
WorkingDirectory=/opt/atlasclaw/AtlasClaw-Core
Environment="PATH=/opt/atlasclaw/venv/bin"
Environment="NO_PROXY=*"
Environment="ATLASCLAW_CONFIG=/opt/atlasclaw/AtlasClaw-Core/atlasclaw.json"
ExecStart=/opt/atlasclaw/venv/bin/uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable atlasclaw
sudo systemctl start atlasclaw
```

**Log rotation**:

```bash
# /etc/logrotate.d/atlasclaw
/opt/atlasclaw/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 atlasclaw atlasclaw
}
```

### 12.7 Environment Variables Reference

**Core Variables**:

| Variable           | Description                | Default          |
| ------------------ | -------------------------- | ---------------- |
| `ATLASCLAW_CONFIG` | Path to configuration file | `atlasclaw.json` |
| `NO_PROXY`         | Proxy bypass               | `*`              |
| `LOG_LEVEL`        | Logging level              | `INFO`           |

**LLM Provider Variables**:

| Variable            | Description       | Required                |
| ------------------- | ----------------- | ----------------------- |
| `DEEPSEEK_API_KEY`  | DeepSeek API key  | Yes (if using DeepSeek) |
| `ANTHROPIC_API_KEY` | Anthropic API key | Optional                |
| `OPENAI_API_KEY`    | OpenAI API key    | Optional                |

**Security Variables**:

| Variable        | Description            | Default |
| --------------- | ---------------------- | ------- |
| `SECRET_KEY`    | Application secret key | Random  |
| `ALLOWED_HOSTS` | Allowed hostnames      | `*`     |
| `CORS_ORIGINS`  | CORS allowed origins   | `*`     |

### 12.8 Troubleshooting

**Port already in use**:

```bash
lsof -i :8000       # Find process
kill -9 <PID>        # Kill it
# Or use a different port:
uvicorn app.atlasclaw.main:app --port 8080
```

**Configuration not loading**:

```bash
echo $ATLASCLAW_CONFIG
python -c "import json; json.load(open('atlasclaw.json'))"
```

**LLM API errors**:

```bash
curl -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  https://api.deepseek.com/v1/models
```

**Debug mode**:

```bash
export LOG_LEVEL=DEBUG
uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000 --reload
```

### 12.9 Monitoring & Backup

**Health check**:

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/health?detailed=true
```

**Backup**:

```bash
tar -czf atlasclaw-backup-$(date +%Y%m%d).tar.gz \
  ~/.atlasclaw \
  atlasclaw.json
```

**Restore**:

```bash
tar -xzf atlasclaw-backup-20240310.tar.gz -C /
```

***

## 13. Git & Commit Standards

### 13.1 Conventional Commits

Format: `<type>(<scope>): <summary>`

**Types**:

| Type       | Usage                                                   |
| ---------- | ------------------------------------------------------- |
| `feat`     | New feature                                             |
| `fix`      | Bug fix                                                 |
| `docs`     | Documentation only                                      |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test`     | Adding or modifying tests                               |
| `chore`    | Build process or tooling changes                        |

**Scope**: Module or area affected (e.g., `config`, `auth`, `agent`, `channels`, `tools`, `session`, `memory`, `skills`, `hooks`, `api`, `frontend`)

**Examples**:

```
feat(channels): add WeChat Work channel handler
fix(auth): handle expired JWKS key rotation
docs(config): align webhook env var examples
refactor(session): extract transcript persistence to separate module
test(tools): add sandbox path validation tests
chore(deps): bump pydantic-ai to 0.3.x
```

***

## 14. Code Review Checklist

Before approving any change, verify:

### Correctness

- [ ] Type hints present on all function parameters and return values
- [ ] Async patterns correct (all I/O operations are async, proper `await`)
- [ ] No bare `except:` — all exceptions are specific
- [ ] Error messages include sufficient context
- [ ] No `print()` statements (use logging, except in hook system)

### Security

- [ ] Permission inheritance maintained — no internal RBAC bypass
- [ ] No hardcoded secrets (API keys, passwords, tokens)
- [ ] User tokens propagated via `SkillDeps.user_token` for upstream calls
- [ ] Tenant isolation maintained (no cross-tenant data access)
- [ ] Tool filtering via `ToolPolicy` respected
- [ ] External input validated at system boundaries

### Architecture

- [ ] Follows thin core / rich providers principle
- [ ] New dependencies passed via `SkillDeps` (not global state)
- [ ] Registry pattern used for new pluggable components
- [ ] Hook phases used for cross-cutting concerns (not inline logic)

### Testing

- [ ] Tests added for new functionality
- [ ] Existing tests pass (`pytest tests/atlasclaw -q`)
- [ ] Async tests use `@pytest.mark.asyncio`
- [ ] Appropriate test markers applied (`slow`, `integration`, `e2e`, `llm`)

### Documentation

- [ ] Public classes and methods have docstrings
- [ ] Module docstrings present on new modules
- [ ] SKILL.md / PROVIDER.md updated for new skills/providers
- [ ] Architecture/module docs updated if API surface changed
- [ ] Configuration changes documented with environment variable format

### Style

- [ ] Follows naming conventions (snake\_case, PascalCase, SCREAMING\_SNAKE\_CASE)
- [ ] Line length \~100 characters
- [ ] Import order correct (stdlib → third-party → local)
- [ ] `from __future__ import annotations` present in new files
- [ ] No commented-out code
- [ ] No wildcard imports

***

> **Cross-Reference Index**
>
> | Topic                    | Document                              | Section                    |
> | ------------------------ | ------------------------------------- | -------------------------- |
> | System architecture      | [Architecture](./ARCHITECTURE.MD)     | §2 High-Level Architecture |
> | Directory structure      | [Architecture](./ARCHITECTURE.MD)     | §3 Directory Structure     |
> | Request lifecycle        | [Architecture](./ARCHITECTURE.MD)     | §5 Request Lifecycle       |
> | Per-module API reference | [Module Details](./MODULE-DETAILS.MD) | All sections               |
> | Configuration schema     | [Module Details](./MODULE-DETAILS.MD) | §1.3 Configuration Schema  |
> | Provider creation guide  | [Provider Guide](./PROVIDER-GUIDE.MD) | Full document              |
> | Skill creation guide     | [Skill Guide](./SKILL-GUIDE.MD)       | Full document              |
> | Channel integration      | [Channel Guide](./CHANNEL-GUIDE.MD)   | Full document              |

