# AtlasClaw Agent Guide

> **DEPRECATED**: This document has been superseded by [Architecture](./ARCHITECTURE.MD) §3 (Directory Structure).
> Please refer to the canonical architecture document.

This document describes the relationship between Users, Workspaces, Agents, Providers, Skills, and Channels in the AtlasClaw enterprise AI agent framework.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AtlasClaw System                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌───────────┐ │
│  │   Channel   │────▶│   Session   │────▶│    Agent    │────▶│   Skill   │ │
│  │  Adapters   │     │   Manager   │     │   Runner    │     │  Registry │ │
│  └─────────────┘     └─────────────┘     └─────────────┘     └───────────┘ │
│         │                   │                   │                   │       │
│         │                   │                   │                   │       │
│         ▼                   ▼                   ▼                   ▼       │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌───────────┐ │
│  │  WebSocket  │     │  User Dir   │     │  SOUL.md    │     │  Provider │ │
│  │    REST     │     │  sessions/  │     │ IDENTITY.md │     │   Skills  │ │
│  │    SSE      │     │  memory/    │     │  USER.md    │     └───────────┘ │
│  └─────────────┘     └─────────────┘     │ MEMORY.md   │          │        │
│                                          └─────────────┘          │        │
│                                             │                      │        │
│                                             ▼                      ▼        │
│                                        ┌─────────────┐     ┌───────────┐   │
│                                        │ AgentLoader │     │  Jira     │   │
│                                        └─────────────┘     │ Confluence│   │
│                                                            │  GitHub   │   │
│                                                            └───────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Concepts

### 1. User

A **User** represents an authenticated entity in the AtlasClaw system.

**Key Characteristics:**
- Identified by a unique DB user ID (UUID)
- Linked to external identity providers (OAuth, OIDC, SAML)
- Has isolated storage and session data
- Uses DB-backed roles and permissions

**User Lifecycle:**
1. User authenticates via external provider (OAuth/OIDC/SAML)
2. Auth helpers create or retrieve the matching DB `users` row
3. `UserWorkspaceInitializer` creates user directory structure
4. User sessions and memory are isolated by `user_id`

---

### 2. Workspace

A **Workspace** is the root organizational unit containing all AtlasClaw resources.

**Key Characteristics:**
- Contains system-wide configuration and resources
- Provides isolation between different deployments
- Initialized automatically on application startup

**Complete Directory Structure:**

```
<project_root>/
├── providers/                           # External providers (enterprise integrations)
│   └── <provider_name>/
│       ├── PROVIDER.md
│       ├── auth/
│       ├── channels/
│       └── skills/
├── skills/                              # External standalone skills (third-party)
│   └── <skill_name>/
│       └── SKILL.md
├── channels/                            # External system-level channel configs
│   └── <channel_name>/
│       └── config.json
├── .atlasclaw/                          # Workspace directory (default)
│   ├── atlasclaw.json                   # Core configuration
│   ├── agents/                          # Agent definitions
│   │   └── main/                        # Default agent
│   │       ├── SOUL.md                  # System prompt & capabilities
│   │       ├── IDENTITY.md              # Agent identity
│   │       ├── USER.md                  # Interaction style
│   │       └── MEMORY.md                # Memory strategy
│   └── users/                           # User directories
│       └── <user-id>/                   # Per-user directory
│           ├── user_setting.json        # User-specific configuration
│           ├── sessions/                # Session storage
│           │   ├── sessions.json        # Session metadata
│           │   ├── <session-id>.jsonl   # Transcript files
│           │   └── archive/             # Archived transcripts
│           └── memory/                  # Long-term memory storage
├── atlasclaw.json                       # Project configuration (symlink or copy)
└── .gitignore                           # Git ignore rules
```

**Initialization:**
- `WorkspaceInitializer` creates the `.atlasclaw` structure
- `UserWorkspaceInitializer` creates per-user directories
- Default `main` agent is created if it doesn't exist

---

### 3. Agent

An **Agent** is an AI assistant configuration defined by Markdown files.

**Definition Files:**

| File | Purpose | Key Sections |
|------|---------|--------------|
| `SOUL.md` | Core capabilities | System Prompt, Capabilities, Providers, Skills |
| `IDENTITY.md` | Agent personality | Display Name, Avatar, Tone |
| `USER.md` | Interaction style | Personalization, Proactive Behaviors |
| `MEMORY.md` | Memory strategy | Long-term Memory, Context Management |

**AgentConfig Structure:**
```python
@dataclass
class AgentConfig:
    agent_id: str
    name: str
    version: str
    system_prompt: str          # From SOUL.md
    capabilities: list[str]     # From SOUL.md
    allowed_providers: list[str] # From SOUL.md
    allowed_skills: list[str]   # From SOUL.md
    display_name: str           # From IDENTITY.md
    avatar: str                 # From IDENTITY.md
    tone: str                   # From IDENTITY.md
    interaction_style: str      # From USER.md
    memory_strategy: str        # From MEMORY.md
    max_context_rounds: int     # From MEMORY.md (default: 20)
```

**Loading Process:**
1. `AgentLoader` scans `.atlasclaw/agents/<agent_id>/`
2. `AgentDefinitionParser` extracts data from Markdown files
3. Configuration is merged with defaults
4. `AgentConfig` object is returned

---

### 4. Provider

A **Provider** is an external service integration (e.g., Jira, Confluence, GitHub).

**Key Characteristics:**
- Provides authentication and API access to external services
- Can expose skills specific to that service
- Multiple instances can be configured per provider type
- User permissions are inherited from external identity

**Provider Structure:**
```
app/atlasclaw/providers/
└── <provider_name>/
    ├── __init__.py
    ├── provider.py              # Provider implementation
    ├── skills/                  # Provider-specific skills
    │   └── <skill_name>/
    │       ├── SKILL.md         # Skill metadata
    │       └── scripts/         # Skill implementation
    └── config.json              # Default configuration
```

**Provider Lifecycle:**
1. Provider is registered in the system
2. User configures provider instance with credentials
3. Provider skills are loaded into `SkillRegistry`
4. Agent can invoke provider skills during conversation

---

### 5. Channel

A **Channel** is a communication interface between users and Agents.

**Supported Channels:**

| Channel | Protocol | Use Case |
|---------|----------|----------|
| **WebSocket** | Real-time bidirectional | Web UI, interactive chat |
| **REST** | HTTP request/response | API integrations, callbacks |
| **SSE** | Server-Sent Events | Streaming responses |

**ChannelAdapterRegistry:**
- Manages adapter instances
- Factory pattern for creating adapters
- Supports custom channel types

**Channel Flow:**
```
User ──▶ Channel Adapter ──▶ Session Manager ──▶ Agent Runner ──▶ Skill Registry
```

---

## Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Relationship Overview                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Workspace (.atlasclaw/)                                                   │
│   │                                                                         │
│   ├── agents/                                                               │
│   │   └── main/                                                             │
│   │       ├── SOUL.md ──────┐                                               │
│   │       ├── IDENTITY.md ──┼──▶ AgentConfig ──┐                           │
│   │       ├── USER.md ──────┤                  │                           │
│   │       └── MEMORY.md ────┘                  │                           │
│   │                                            │                           │
│   ├── providers/                               │                           │
│   │   └── jira/                                │                           │
│   │       └── skills/ ─────────────────────────┼────▶ SkillRegistry        │
│   │                                            │                           │
│   └── skills/ ─────────────────────────────────┘                           │
│                                                                             │
│   users/                                                                    │
│   └── <user_id>/                                                            │
│       ├── sessions/ ────┐                                                   │
│       │   └── <session> │                                                   │
│       ├── memory/       ├──▶ SessionManager ──┐                            │
│       └── channels/     │                     │                            │
│                         │                     │                            │
│   Channel Adapters ◀────┘                     │                            │
│        │                                      │                            │
│        └──────────────────────────────────────┼────▶ Agent Runner          │
│                                               │                            │
└───────────────────────────────────────────────┼────────────────────────────┘
                                                │
                                                ▼
                                         ┌─────────────┐
                                         │   LLM API   │
                                         └─────────────┘
```

## Data Flow

### 1. User Authentication Flow

```
1. User authenticates via OAuth/OIDC/SAML
2. Auth provider returns AuthResult (subject, email, name)
3. DB user helper resolves the authenticated subject:
   - Check if a matching `users.username` exists
   - If not, create a DB user with the default standard role
   - Initialize user workspace directory
4. Return UserInfo with the DB user ID
```

### 2. Session Creation Flow

```
1. Channel Adapter receives message with session_key
2. SessionManager.get_or_create(session_key):
   - Parse session_key: agent_id:channel:account_id[:thread_id]
   - Check for existing session
   - Apply reset policy (DAILY/IDLE/MANUAL)
   - Create new session if needed
3. Load or create transcript file
4. Return SessionMetadata
```

### 3. Agent Execution Flow

```
1. Channel Adapter receives user message
2. SessionManager loads/creates session
3. AgentLoader loads AgentConfig from Markdown files
4. PromptBuilder constructs system prompt
5. Agent Runner executes with:
   - System prompt
   - Conversation history
   - Available skills (from SkillRegistry)
6. SkillRegistry executes tools as needed
7. Response streamed back through Channel Adapter
```

## Configuration Hierarchy

Configuration is loaded in the following priority order (highest to lowest):

```
1. Workspace-level (.atlasclaw/)
   - Agent definitions (SOUL.md, etc.)
   - Provider configurations
   - Workspace skills

2. User-level (users/<user_id>/)
   - User-specific settings (atlasclaw.json)
   - Channel configurations
   - Session data
   - Long-term memory

3. Global-level (~/.atlasclaw/)
   - Global skills

4. Default built-in
   - Default agent configuration
   - Built-in skills
```

## Security Model

### Permission Inheritance

```
External Identity Provider
    │
    ▼
DB User (user_id)
    │
    ├──▶ Session (isolated per user)
    │
    ├──▶ Memory (isolated per user)
    │
    └──▶ Role-based access
```

### Key Security Principles

1. **User Isolation**: Each user's sessions and memory are isolated by `user_id`
2. **Permission Inheritance**: Provider access uses user's external identity permissions
3. **No Privilege Escalation**: Agents cannot access resources beyond user's permissions
4. **Audit Trail**: All operations are traceable to user and session

## Best Practices

### Agent Definition

- Keep `SOUL.md` system prompt concise and focused
- Use `IDENTITY.md` to define consistent personality
- Configure appropriate `max_context_rounds` in `MEMORY.md`
- Document all available providers and skills

### Provider Integration

- Store credentials securely (user-level config)
- Implement proper error handling
- Respect rate limits
- Cache responses when appropriate

### Channel Implementation

- Handle reconnection gracefully
- Implement proper error responses
- Support message acknowledgment
- Log channel events

## API Reference

### Key Classes

| Class | Module | Purpose |
|-------|--------|---------|
| `AgentLoader` | `agent.agent_definition` | Load agent configs from Markdown |
| `AgentConfig` | `agent.agent_definition` | Agent configuration dataclass |
| `SkillRegistry` | `skills.registry` | Register and execute skills |
| `SessionManager` | `session.manager` | Manage session lifecycle |
| `UserService` | `db.orm.user` | Manage DB-backed users |
| `WorkspaceInitializer` | `core.workspace` | Initialize workspace structure |
| `ChannelAdapterRegistry` | `channels.registry` | Manage channel adapters |

### Key Functions

| Function | Module | Purpose |
|----------|--------|---------|
| `ensure_db_user_for_auth_result()` | `auth.user_store` | Resolve external auth results to DB users |
| `load_agent()` | `agent.agent_definition` | Load agent configuration |
| `execute()` | `skills.registry` | Execute a skill |
| `get_or_create_session()` | `session.manager` | Manage sessions |
| `initialize()` | `core.workspace` | Setup workspace |

---

*For more details, see the architecture documentation in `docs/PROJECT_OVERVIEW.md`.*
