# AtlasClaw Development Specification

> **Canonical development standards for the AtlasClaw enterprise agent framework.**
> All future development and feature changes MUST follow these specifications.
>
> This document consolidates content previously in `CODE_STRUCTURE.md`, `CODING_STANDARDS.md`,
> and `DEPLOYMENT.md`. Those files are no longer maintained separately.
>
> Related documents:
> - [Architecture](./architecture.md) 鈥?system design, bootstrap, request lifecycle
> - [Module Details](./module-details.md) 鈥?per-module API surface and internals

---

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
---

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

---

## 2. Project Structure

> Also see [Architecture](./architecture.md) 搂3 for a high-level directory overview.

### 2.1 Directory Tree

```
AtlasClaw-Core/
鈹溾攢鈹€ app/
鈹?  鈹溾攢鈹€ frontend/                    # Web UI (DeepChat-based)
鈹?  鈹?  鈹溾攢鈹€ index.html              # Main entry point
鈹?  鈹?  鈹溾攢鈹€ scripts/                # JavaScript modules
鈹?  鈹?  鈹?  鈹溾攢鈹€ api-client.js       # API client
鈹?  鈹?  鈹?  鈹溾攢鈹€ session-manager.js  # Session management
鈹?  鈹?  鈹?  鈹溾攢鈹€ message-handler.js  # Message handling
鈹?  鈹?  鈹?  鈹斺攢鈹€ ui-components.js    # UI components
鈹?  鈹?  鈹溾攢鈹€ styles/                 # CSS styles
鈹?  鈹?  鈹溾攢鈹€ locales/                # i18n translations
鈹?  鈹?  鈹斺攢鈹€ static/                 # Static assets
鈹?  鈹?
鈹?  鈹斺攢鈹€ atlasclaw/                    # Core backend
鈹?      鈹溾攢鈹€ __init__.py
鈹?      鈹溾攢鈹€ main.py                 # FastAPI application entry
鈹?      鈹?
鈹?      鈹溾攢鈹€ agent/                  # Agent Engine
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ runner.py           # Agent execution engine
鈹?      鈹?  鈹溾攢鈹€ routing.py          # Multi-agent routing
鈹?      鈹?  鈹溾攢鈹€ prompt_builder.py   # System prompt builder
鈹?      鈹?  鈹溾攢鈹€ stream.py           # Streaming events & chunking
鈹?      鈹?  鈹斺攢鈹€ compaction.py       # Context compaction
鈹?      鈹?
鈹?      鈹溾攢鈹€ api/                    # API Layer
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ routes.py           # REST API routes
鈹?      鈹?  鈹溾攢鈹€ gateway.py          # WebSocket gateway
鈹?      鈹?  鈹溾攢鈹€ sse.py              # SSE manager
鈹?      鈹?  鈹溾攢鈹€ websocket.py        # WebSocket handler
鈹?      鈹?  鈹斺攢鈹€ request_orchestrator.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ auth/                   # Authentication System
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ middleware.py       # Auth middleware
鈹?      鈹?  鈹溾攢鈹€ strategy.py         # Auth strategy
鈹?      鈹?  鈹溾攢鈹€ models.py           # User models
鈹?      鈹?  鈹溾攢鈹€ config.py           # Auth configuration
鈹?      鈹?  鈹溾攢鈹€ shadow_store.py     # Shadow user store
鈹?      鈹?  鈹斺攢鈹€ providers/          # Auth providers
鈹?      鈹?      鈹溾攢鈹€ __init__.py
鈹?      鈹?      鈹溾攢鈹€ base.py
鈹?      鈹?      鈹溾攢鈹€ none.py
鈹?      鈹?      鈹溾攢鈹€ local.py        # Username/password auth
鈹?      鈹?      鈹溾攢鈹€ oidc_jwt.py     # JWT validation for API
鈹?      鈹?      鈹斺攢鈹€ oidc_login.py   # OAuth2 + PKCE for SSO
鈹?      鈹?
鈹?      鈹溾攢鈹€ channels/               # Channel Adapters
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ base.py
鈹?      鈹?  鈹溾攢鈹€ registry.py
鈹?      鈹?  鈹溾攢鈹€ rest_adapter.py
鈹?      鈹?  鈹溾攢鈹€ sse_adapter.py
鈹?      鈹?  鈹斺攢鈹€ websocket_adapter.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ core/                   # Core Infrastructure
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ config.py           # Configuration manager
鈹?      鈹?  鈹溾攢鈹€ config_schema.py    # Pydantic config schemas
鈹?      鈹?  鈹溾攢鈹€ deps.py             # Skill dependency injection
鈹?      鈹?  鈹溾攢鈹€ execution_context.py
鈹?      鈹?  鈹溾攢鈹€ provider_registry.py
鈹?      鈹?  鈹斺攢鈹€ tenant.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ hooks/                  # Hook System
鈹?      鈹?  鈹斺攢鈹€ system.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ media/                  # Media Processing
鈹?      鈹?  鈹溾攢鈹€ link_extractor.py
鈹?      鈹?  鈹溾攢鈹€ tts.py
鈹?      鈹?  鈹斺攢鈹€ understanding.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ memory/                 # Memory System
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ manager.py
鈹?      鈹?  鈹斺攢鈹€ search.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ messages/               # Message Processing
鈹?      鈹?  鈹溾攢鈹€ command.py
鈹?      鈹?  鈹斺攢鈹€ handler.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ models/                 # Model Management
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ providers.py
鈹?      鈹?  鈹溾攢鈹€ retry.py
鈹?      鈹?  鈹斺攢鈹€ failover.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ providers/              # Built-in Providers
鈹?      鈹?  鈹斺攢鈹€ jira/               # Jira Provider Example
鈹?      鈹?      鈹溾攢鈹€ skills/
鈹?      鈹?      鈹?  鈹斺攢鈹€ jira-issue/
鈹?      鈹?      鈹?      鈹溾攢鈹€ SKILL.md
鈹?      鈹?      鈹?      鈹斺攢鈹€ scripts/
鈹?      鈹?      鈹斺攢鈹€ scripts/
鈹?      鈹?
鈹?      鈹溾攢鈹€ session/                # Session Management
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ manager.py
鈹?      鈹?  鈹溾攢鈹€ context.py
鈹?      鈹?  鈹斺攢鈹€ queue.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ skills/                 # Skills System
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ registry.py
鈹?      鈹?  鈹斺攢鈹€ frontmatter.py
鈹?      鈹?
鈹?      鈹溾攢鈹€ tools/                  # Built-in Tools
鈹?      鈹?  鈹溾攢鈹€ __init__.py
鈹?      鈹?  鈹溾攢鈹€ base.py
鈹?      鈹?  鈹溾攢鈹€ catalog.py
鈹?      鈹?  鈹溾攢鈹€ registration.py
鈹?      鈹?  鈹溾攢鈹€ filesystem/         # File system tools
鈹?      鈹?  鈹溾攢鈹€ memory/             # Memory tools
鈹?      鈹?  鈹溾攢鈹€ providers/          # Provider tools
鈹?      鈹?  鈹溾攢鈹€ runtime/            # Runtime tools
鈹?      鈹?  鈹溾攢鈹€ sessions/           # Session tools
鈹?      鈹?  鈹溾攢鈹€ ui/                 # UI tools
鈹?      鈹?  鈹斺攢鈹€ web/                # Web tools
鈹?      鈹?
鈹?      鈹斺攢鈹€ workflow/               # Workflow Engine
鈹?          鈹溾攢鈹€ __init__.py
鈹?          鈹溾攢鈹€ engine.py
鈹?          鈹斺攢鈹€ orchestrator.py
鈹?
鈹溾攢鈹€ tests/                          # Test Suite
鈹?  鈹溾攢鈹€ conftest.py                 # pytest configuration
鈹?  鈹溾攢鈹€ atlasclaw.test.json           # Test configuration
鈹?  鈹溾攢鈹€ atlasclaw/                    # Python tests
鈹?  鈹?  鈹溾攢鈹€ test_core.py
鈹?  鈹?  鈹溾攢鈹€ test_agent.py
鈹?  鈹?  鈹溾攢鈹€ test_agent_integration.py
鈹?  鈹?  鈹溾攢鈹€ test_e2e_api.py
鈹?  鈹?  鈹溾攢鈹€ auth/
鈹?  鈹?  鈹溾攢鈹€ memory/
鈹?  鈹?  鈹溾攢鈹€ session/
鈹?  鈹?  鈹斺攢鈹€ providers/
鈹?  鈹斺攢鈹€ frontend/                   # Frontend tests
鈹?
鈹溾攢鈹€ docs/                           # Documentation
鈹?  鈹溾攢鈹€ architecture.md
鈹?  鈹溾攢鈹€ module-details.md
鈹?  鈹溾攢鈹€ development-spec.md
鈹?  鈹斺攢鈹€ images/architecture/
鈹?
鈹溾攢鈹€ openspec/                       # OpenSpec Specifications
鈹?  鈹溾攢鈹€ AGENTS.md
鈹?  鈹溾攢鈹€ project.md
鈹?  鈹溾攢鈹€ changes/
鈹?  鈹斺攢鈹€ completed/
鈹?
鈹溾攢鈹€ atlasclaw.json                    # Main configuration
鈹斺攢鈹€ requirements.txt                # Python dependencies
```

### 2.2 Module Dependencies

```
main.py
鈹溾攢鈹€ agent/runner.py
鈹?  鈹溾攢鈹€ core/deps.py
鈹?  鈹溾攢鈹€ session/manager.py
鈹?  鈹斺攢鈹€ skills/registry.py
鈹溾攢鈹€ api/routes.py
鈹?  鈹溾攢鈹€ agent/runner.py
鈹?  鈹溾攢鈹€ auth/middleware.py
鈹?  鈹斺攢鈹€ session/manager.py
鈹溾攢鈹€ auth/middleware.py
鈹?  鈹斺攢鈹€ auth/strategy.py
鈹溾攢鈹€ core/config.py
鈹?  鈹斺攢鈹€ core/config_schema.py
鈹斺攢鈹€ skills/registry.py
    鈹斺攢鈹€ tools/registration.py
```

### 2.3 Data Flow

```
User Request
    鈫?
API Layer (routes.py)
    鈫?
Auth Middleware
    鈫?
Agent Runner
    鈫?
Skill Execution
    鈫?
Provider API
    鈫?
Response Stream (SSE)
```

### 2.4 Key Files

**Entry Points**:

| File | Purpose |
|------|---------|
| `app/atlasclaw/main.py` | FastAPI application entry |
| `app/frontend/index.html` | Web UI entry |

**Configuration**:

| File | Purpose |
|------|---------|
| `atlasclaw.json` | Main configuration file |
| `tests/atlasclaw.test.json` | Test configuration |
| `app/atlasclaw/core/config_schema.py` | Configuration schemas |

**Core Logic**:

| File | Purpose |
|------|---------|
| `app/atlasclaw/agent/runner.py` | Agent execution engine |
| `app/atlasclaw/skills/registry.py` | Skills registration |
| `app/atlasclaw/session/manager.py` | Session management |
| `app/atlasclaw/memory/manager.py` | Memory management |

**API**:

| File | Purpose |
|------|---------|
| `app/atlasclaw/api/routes.py` | REST API routes |
| `app/atlasclaw/api/sse.py` | SSE streaming |
| `app/atlasclaw/api/websocket.py` | WebSocket handler |

### 2.5 File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Modules | `snake_case.py` | `runner.py` |
| Classes | `PascalCase` | `AgentRunner` |
| Functions | `snake_case` | `run_agent` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRY` |
| Tests | `test_*.py` | `test_runner.py` |
| Test Classes | `Test*` | `TestAgentRunner` |
| Test Methods | `test_*` | `test_run` |

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
workspace/users/<user_id>/sessions/
鈹溾攢鈹€ sessions.json
鈹溾攢鈹€ <session_id>.jsonl
鈹溾攢鈹€ <session_id>-topic-<thread_id>.jsonl
鈹斺攢鈹€ archive/
```

Session routing rules:

- Always resolve the storage bucket from the canonical `SessionKey.user_id`
- Web chat uses a new thread endpoint for "New Chat" instead of reusing the stable `main` scope
- Channel adapters must build canonical `SessionKey` values instead of hand-crafted keys
- Direct session endpoints must reject access when the session owner does not match the authenticated user

**Memory storage**:
```
workspace/users/<user_id>/memory/
鈹溾攢鈹€ memory_<timestamp>.md
鈹溾攢鈹€ vector/
鈹斺攢鈹€ fulltext/
```

**Configuration storage**:
```
~/.atlasclaw/
鈹溾攢鈹€ config.json
鈹斺攢鈹€ workspace/
```

---

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
- Line length: ~100 characters (be reasonable, not dogmatic)
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

| Element | Convention | Example |
|---|---|---|
| Functions, variables | `snake_case` | `resolve_user`, `session_key` |
| Classes, exceptions | `PascalCase` | `AgentRunner`, `AuthenticationError` |
| Constants | `SCREAMING_SNAKE_CASE` | `ANONYMOUS_USER`, `GROUP_FS` |
| Private members | `_` prefix | `_hooks`, `_build_prompt` |
| Modules | `snake_case` | `shadow_store.py`, `config_schema.py` |

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
        provider: Auth provider name (e.g., "local", "oidc_jwt", "oidc_login").
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

---

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

---

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

---

## 6. Security Requirements

### 6.1 Permission Inheritance

**Rule**: AtlasClaw may enforce workspace-local RBAC for management APIs, but runtime agent actions MUST still inherit the authenticated user's permissions from the upstream system.

```python
# Correct: Pass user's token to upstream API
headers = {"Authorization": f"Bearer {deps.user_token}"}
resp = await client.get("/api/resource", headers=headers)

# Wrong: bypass upstream permissions for runtime actions
if user.has_role("admin"):  # Never do this
    ...
```

### 6.2 Token Propagation

- `SkillDeps.user_info.raw_token` is always available to skills and tools
- Always use `deps.user_token` (or `deps.user_info.raw_token`) for upstream API calls
- Never cache tokens beyond the auth strategy's TTL

### 6.3 Tool Filtering

Use `ToolPolicy` to restrict tool access per agent:
- `allow: ["*"]` 鈥?All tools (default)
- `deny: ["exec", "process"]` 鈥?Block specific tools
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

---


### 6.7 Tool Grounding and Anti-Fabrication

When the runtime classifies a request as tool-required, the code must not allow an ungrounded
final answer to pass through just because the model produced fluent text.

Required rules:
- Prefer explicit classifier-driven tool-gate decisions over hard-coded business keyword tables.
- Model-backed gate classification must be explicitly enabled; do not add hidden blocking classifier round-trips to every request by default.
- If mandatory capabilities are missing, return an explicit verification failure instead of a
  guessed answer.
- Do not claim search, lookup, provider retrieval, or browser interaction happened unless tool
  execution evidence exists in the current run.
- Treat tool results as privileged evidence for the current turn when enforcement requires
  grounded answering.
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

---

## 8. Testing Standards

### 8.1 File Structure

| Convention | Pattern | Example |
|---|---|---|
| Test files | `test_<module>.py` | `test_agent.py` |
| Test classes | `Test<PascalCase>` | `TestStreamEvent` |
| Test methods | `test_<description>` | `test_create_lifecycle_start` |
| Fixtures | `conftest.py` | Shared fixtures at test root |

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

| Marker | Usage |
|---|---|
| `@pytest.mark.slow` | Tests taking > 1 second |
| `@pytest.mark.integration` | Integration tests requiring running services |
| `@pytest.mark.e2e` | End-to-end tests requiring full stack |
| `@pytest.mark.llm` | Tests requiring LLM API calls |
| `@pytest.mark.asyncio` | Async tests (required for all `async def` tests) |

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

---

## 9. Extension Development

### 9.1 Adding a Provider

**Directory structure**:
```
<providers_root>/<provider_name>/
鈹溾攢鈹€ PROVIDER.md       # Required: documentation + LLM context
鈹斺攢鈹€ skills/
    鈹溾攢鈹€ skill-one/
    鈹?  鈹溾攢鈹€ SKILL.md  # Required: skill documentation
    鈹?  鈹斺攢鈹€ handler.py  # Optional: executable skill
    鈹斺攢鈹€ skill-two/
        鈹斺攢鈹€ SKILL.md  # Markdown-only skill
```

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

### 9.3 Adding a Hook Runtime Consumer

Use the generic hook runtime when a capability needs lifecycle observation,
pending confirmation, or controlled memory/context integration.

**Core rule**:
- Keep the core generic
- Do not add skill-specific event types, pending schemas, or storage layouts to `app/atlasclaw/hooks/`

**What belongs in the hook runtime**:
- Generic lifecycle events
- Generic aggregated runtime events such as `run.context_ready`
- Generic dispatch and registration
- Generic per-user module state storage
- Generic memory and context sinks
- Generic script hook protocol and action application

**What does not belong in the hook runtime**:
- Module-specific learning logic
- Skill-specific promotion rules
- Provider-specific business semantics

**Phase 1 registration pattern**:
```python
hook_runtime.register(
    module_name="runtime-audit",
    handler=my_handler,
    event_types={HookEventType.RUN_FAILED, HookEventType.TOOL_FAILED},
)
```

**Config-driven script pattern**:
```json
{
  "hooks_runtime": {
    "script_handlers": [
      {
        "module": "runtime-audit-script",
        "events": ["run.context_ready"],
        "command": ["python", "scripts/hook_consumer.py"],
        "timeout_seconds": 10,
        "enabled": true
      }
    ]
  }
}
```

**Script output rules**:
- Input arrives as `HookEventEnvelope` JSON over `stdin`
- Output must be JSON with `actions: []`
- Only generic actions are allowed:
  - `create_pending`
  - `write_memory`
  - `add_context`
- Script hook failures must not fail the primary agent run

If a skill wants to use hook data, it should consume the runtime as an external capability
rather than being hard-coded into the core.

### 9.4 Adding a Channel Handler

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

### 9.5 Adding an Auth Provider

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

---

## 10. Configuration Standards

### 10.1 Adding New Configuration

1. Define a Pydantic model in `core/config_schema.py`:

```python
class MyFeatureConfig(BaseModel):
    """My feature configuration."""
    enabled: bool = False
    max_retries: int = Field(default=3, ge=1, description="Maximum retry attempts")
```

2. Add the field to `AtlasClawConfig`:

```python
class AtlasClawConfig(BaseModel):
    ...
    my_feature: MyFeatureConfig = Field(default_factory=MyFeatureConfig)
```

3. Access in code via `get_config()`:

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

### 10.4 Per-User Overrides

Per-user configuration in `<workspace>/users/<user_id>/atlasclaw.json` is merged with the global config. Only include fields that differ from the default.

---

## 11. Tools & Automation

### 11.1 Recommended Tools

| Tool | Purpose |
|---|---|
| **Ruff** | Fast Python linter and formatter |
| **mypy** | Static type checker |
| **pytest** | Testing framework |
| **pre-commit** | Git hooks for code quality |

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

### 11.3 Heartbeat Runtime Standards

When implementing or extending heartbeat functionality:

- keep heartbeat scheduling separate from Hook Runtime scheduling
- use `HeartbeatRuntime` typed jobs instead of ad-hoc background loops
- emit standard heartbeat events before any notification delivery
- persist per-user heartbeat state under `workspace/users/<user_id>/heartbeat/`
- treat channel heartbeat as self-healing first, alerting second
- preserve OpenClaw-aligned semantics for agent heartbeat where applicable:
  - `HEARTBEAT.md`
  - `every`
  - `activeHours`
  - `isolatedSession`
  - `HEARTBEAT_OK`

Required verification for heartbeat changes:

- unit tests for config/models/store/targets/runtime
- integration coverage for hook event bridging
- end-to-end coverage for runtime execution and persisted state
- full application E2E pass with a live server before claiming completion

---

## 12. Deployment & Operations

### 12.1 Prerequisites

**System Requirements**:

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8+ GB |
| Disk | 10 GB | 50+ GB SSD |
| Python | 3.10+ | 3.11+ |

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

**Database Configuration**:

```json
{
  "database": {
    "type": "sqlite",
    "sqlite": {
      "path": "./data/atlasclaw.db"
    },
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

| Mode | Type | Initialization |
|---|---|---|
| Development/Single-node | SQLite | Auto-create via ORM on startup |
| Enterprise/Multi-node | MySQL | Requires `alembic upgrade head` |

Create `.env` file for secrets:

```bash
# LLM API Keys
DEEPSEEK_API_KEY=your-api-key-here

# MySQL (only needed for enterprise mode)
MYSQL_HOST=localhost
MYSQL_DATABASE=atlasclaw
MYSQL_USER=atlasclaw
MYSQL_PASSWORD=your-password

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

**Dockerfile** (`docker/Dockerfile`):
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini .

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Health check (note: /api/health is the correct endpoint)
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Run migrations and start server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000"]
```

```bash
# Build from docker directory
docker build -f docker/Dockerfile -t atlasclaw-core .

# Run with config
docker run -d \
  --name atlasclaw \
  -p 8000:8000 \
  -v $(pwd)/atlasclaw.json:/app/atlasclaw.json:ro \
  -e ATLASCLAW_ENCRYPTION_KEY=your-key \
  atlasclaw-core
```

**Docker Compose** (see `docker/` directory for complete configurations):

**SQLite mode** (`docker/docker-compose.sqlite.yml`):
```yaml
version: '3.8'

services:
  atlasclaw:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    ports:
      - "8000:8000"
    environment:
      - ATLASCLAW_ENCRYPTION_KEY=${ATLASCLAW_ENCRYPTION_KEY:-change-me-in-production}
    volumes:
      - ../atlasclaw.json:/app/atlasclaw.json:ro
      - atlasclaw-data:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s

volumes:
  atlasclaw-data:
```

**MySQL mode** (`docker/docker-compose.yml`):
```yaml
version: '3.8'

services:
  atlasclaw:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    ports:
      - "8000:8000"
    environment:
      - ATLASCLAW_ENCRYPTION_KEY=${ATLASCLAW_ENCRYPTION_KEY:-change-me-in-production}
      - DB_HOST=mysql
      - DB_PORT=3306
      - DB_NAME=atlasclaw
      - DB_USER=${DB_USER:-atlasclaw}
      - DB_PASSWORD=${DB_PASSWORD:-atlasclaw_secret}
    depends_on:
      mysql:
        condition: service_healthy
    volumes:
      - ../atlasclaw.json:/app/atlasclaw.json:ro
      - atlasclaw-data:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  mysql:
    image: mysql:8.0
    environment:
      - MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD:-root_secret}
      - MYSQL_DATABASE=atlasclaw
      - MYSQL_USER=${DB_USER:-atlasclaw}
      - MYSQL_PASSWORD=${DB_PASSWORD:-atlasclaw_secret}
    ports:
      - "3306:3306"
    volumes:
      - mysql-data:/var/lib/mysql
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-p${MYSQL_ROOT_PASSWORD:-root_secret}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s

volumes:
  mysql-data:
  atlasclaw-data:
```

### 12.6 Database Migrations (MySQL Mode)

For enterprise deployments using MySQL, run migrations before starting the service:

```bash
# Set database environment variables
export MYSQL_HOST=localhost
export MYSQL_DATABASE=atlasclaw
export MYSQL_USER=atlasclaw
export MYSQL_PASSWORD=your-password

# Run Alembic migrations
alembic upgrade head

# Start service
uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000
```

**Migration Files**:
- `migrations/versions/001_initial.py` 鈥?Initial schema
- `migrations/versions/002_add_channel_is_default.py` 鈥?Add channel defaults
- `migrations/versions/003_add_service_provider_configs.py` 鈥?Add provider configs

### 12.7 Production Deployment

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

    # SSE endpoint 鈥?disable buffering
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

| Variable | Description | Default |
|----------|-------------|---------|
| `ATLASCLAW_CONFIG` | Path to configuration file | `atlasclaw.json` |
| `NO_PROXY` | Proxy bypass | `*` |
| `LOG_LEVEL` | Logging level | `INFO` |

**LLM Provider Variables**:

| Variable | Description | Required |
|----------|-------------|----------|
| `DEEPSEEK_API_KEY` | DeepSeek API key | Yes (if using DeepSeek) |
| `ANTHROPIC_API_KEY` | Anthropic API key | Optional |
| `OPENAI_API_KEY` | OpenAI API key | Optional |

**Database Variables** (for Docker/MySQL deployments):

| Variable | Description | Default |
|----------|-------------|---------|
| `ATLASCLAW_ENCRYPTION_KEY` | Encryption key for sensitive data | Required in Docker |
| `DB_HOST` | Database host | `mysql` |
| `DB_PORT` | Database port | `3306` |
| `DB_NAME` | Database name | `atlasclaw` |
| `DB_USER` | Database user | `atlasclaw` |
| `DB_PASSWORD` | Database password | Required |
| `MYSQL_ROOT_PASSWORD` | MySQL root password (compose only) | `root_secret` |

**Security Variables**:

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Application secret key | Random |
| `ALLOWED_HOSTS` | Allowed hostnames | `*` |
| `CORS_ORIGINS` | CORS allowed origins | `*` |

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

---

## 13. Git & Commit Standards

### 13.1 Conventional Commits

Format: `<type>(<scope>): <summary>`

**Types**:

| Type | Usage |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or modifying tests |
| `chore` | Build process or tooling changes |

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

---

## 14. Code Review Checklist

Before approving any change, verify:

### Correctness
- [ ] Type hints present on all function parameters and return values
- [ ] Async patterns correct (all I/O operations are async, proper `await`)
- [ ] No bare `except:` 鈥?all exceptions are specific
- [ ] Error messages include sufficient context
- [ ] No `print()` statements (use logging, except in hook system)

### Security
- [ ] Permission inheritance maintained 鈥?no internal RBAC bypass
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
- [ ] Follows naming conventions (snake_case, PascalCase, SCREAMING_SNAKE_CASE)
- [ ] Line length ~100 characters
- [ ] Import order correct (stdlib 鈫?third-party 鈫?local)
- [ ] `from __future__ import annotations` present in new files
- [ ] No commented-out code
- [ ] No wildcard imports

---

> **Cross-Reference Index**
>
> | Topic | Document | Section |
> |---|---|---|
> | System architecture | [Architecture](./architecture.md) | 搂2 High-Level Architecture |
> | Directory structure | [Architecture](./architecture.md) | 搂3 Directory Structure |
> | Request lifecycle | [Architecture](./architecture.md) | 搂5 Request Lifecycle |
> | Per-module API reference | [Module Details](./module-details.md) | All sections |
> | Configuration schema | [Module Details](./module-details.md) | 搂1.3 Configuration Schema |
> | Provider creation guide | [Provider Guide](./PROVIDER_GUIDE.md) | Full document |
> | Skill creation guide | [Skill Guide](./SKILL_GUIDE.md) | Full document |
> | Channel integration | [Channel Guide](./Channel%20Guide.md) | Full document |
