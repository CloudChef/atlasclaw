# Xuanwu Plugins Extension Guide

This document explains how to extend Xuanwu functionality across three areas: Authentication, Channels, and Skills.

## Design Principles

1. **Built-in + Scannable**: Core Auth and Channel types are built-in, while automatically scanning extensions under the providers directory
2. **Separation of Concerns**: Auth, Channel, and Skills are independent with no forced unified interface
3. **User Isolation**: Channel connections are stored per user, Auth is per configuration instance
4. **Protocol First**: Use Python Protocol and ABC to define contracts, rather than forcing inheritance
5. **Long Connection First**: All Channels prioritize long connections (WebSocket/Socket Mode), Webhook as fallback

## Extension Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Xuanwu Extension Architecture                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │   AuthProvider  │  │  ChannelHandler │  │        Skills               │  │
│  │                 │  │                 │  │                             │  │
│  │ • OIDC          │  │ • Feishu        │  │ • Executable                │  │
│  │ • OAuth2        │  │ • Slack         │  │ • Markdown                  │  │
│  │ • API Key       │  │ • WhatsApp      │  │ • Hybrid                    │  │
│  │ • SAML          │  │ • WebSocket     │  │                             │  │
│  │ • (Provider Scan)│  │ • SSE           │  │                             │  │
│  │                 │  │ • REST          │  │                             │  │
│  │                 │  │ • (Provider Scan)│  │                             │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │
│           │                    │                        │                   │
│           ▼                    ▼                        ▼                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                           Registry                                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │   │
│  │  │AuthRegistry  │  │ChannelRegistry│  │    SkillRegistry         │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Extension Methods

### 1. Provider Directory Structure

All extensions are managed through the `providers` directory:

```
providers/                         # Configured via providers_root (default: ../providers)
└── <provider_name>/              # Provider name
    ├── PROVIDER.md               # Provider documentation
    ├── auth/                     # Auth extension (optional)
    │   └── <auth_type>.py        # AuthProvider implementation
    ├── channels/                 # Channel extension (optional)
    │   └── <channel_type>.py     # ChannelHandler implementation
    └── skills/                   # Skills extension (optional)
        └── <skill_name>/
            ├── SKILL.md
            └── scripts/
```

### 2. Authentication Extension (Auth)

#### Extension Steps

1. Create a Python file under `providers/<provider_name>/auth/` directory
2. Implement the `AuthProvider` abstract base class
3. Set class attribute `auth_id` as the identifier
4. Implement `authenticate()` and `provider_name()` methods

#### Built-in Auth Types

| Type | ID | Description |
|------|-----|-------------|
| OIDC | `oidc` | OpenID Connect |
| OAuth2 | `oauth2` | OAuth 2.0 |
| API Key | `api_key` | Simple API Key |
| SAML | `saml` | SAML 2.0 |
| None | `none` | No authentication (for development) |

#### Extension Example Types

| Type | ID | Description |
|------|-----|-------------|
| LDAP | `ldap` | LDAP authentication |
| AD | `ad` | Active Directory |

#### Auth Configuration Location

Auth configuration is **global**, stored in `xuanwu.json` at the workspace root. All users share the same authentication configuration.

### 3. Channel Extension (Channel)

#### Extension Steps

1. Create a Python file under `providers/<provider_name>/channels/` directory
2. Implement the `ChannelHandler` abstract base class
3. Set class attributes:
   - `channel_type`: Type identifier
   - `channel_name`: Display name
   - `channel_mode`: Working mode (INBOUND/OUTBOUND/BIDIRECTIONAL)
4. Implement required methods:
   - `setup()`: Initialize configuration
   - `start()`: Start channel
   - `stop()`: Stop channel
   - `handle_inbound()`: Handle inbound messages
   - `send_message()`: Send messages

#### Channel Working Modes

| Mode | Description |
|------|-------------|
| INBOUND | Receive only (e.g., Webhook) |
| OUTBOUND | Send only (e.g., SMTP) |
| BIDIRECTIONAL | Two-way (e.g., WebSocket) |

#### Built-in Channel Types

| Type | ID | Transport | Description |
|------|-----|-----------|-------------|
| WebSocket | `websocket` | WebSocket | Browser/client connection |
| SSE | `sse` | SSE | Server push |
| REST | `rest` | HTTP | Callback interface |

#### Extension Channel Types

| Type | ID | Transport | Description |
|------|-----|-----------|-------------|
| Feishu | `feishu` | Webhook/WebSocket | Feishu bot |
| Slack | `slack` | Webhook/Socket | Slack integration |
| WhatsApp | `whatsapp` | WebSocket | WhatsApp Business |
| DingTalk | `dingtalk` | Webhook | DingTalk bot |

#### Channel Configuration Location

Channel configuration is **user-isolated**, stored in user directories:

- **Primary location**: `<workspace>/users/<user_id>/user_setting.json`
- **System-level config**: `<channels_root>/<channel_name>/config.json`

### 4. Skills Extension (Skill)

#### Extension Steps

1. Create a skill directory under `providers/<provider_name>/skills/`
2. Create `SKILL.md` file to define skill metadata and capabilities
3. Place executable scripts in `scripts/` directory

#### Standalone Skills Directory

Standalone skills not tied to a specific Provider can be placed in the `skills/` directory:

```
skills/                           # Configured via skills_root (default: ../skills)
└── <skill_name>/                # Standalone skill
    ├── SKILL.md
    └── scripts/
```

#### Skill Types

| Type | Description |
|------|-------------|
| Executable | Executable scripts |
| Markdown | Pure Markdown definition |
| Hybrid | Mixed type |

## Configuration Storage Rules

| Feature | Config Location | Scope |
|---------|-----------------|-------|
| Authentication (Auth) | `<workspace>/xuanwu.json` | Workspace global |
| Channel | `<workspace>/users/<user_id>/user_setting.json` | User isolated |
| Service Providers | `<workspace>/xuanwu.json` | Workspace global |
| Extension Location | `providers/<name>/` | — |

## Startup Loading Flow

1. Scan Auth extensions → Register to `AuthRegistry`
2. Scan Channel extensions → Register to `ChannelRegistry`
3. Register built-in channels → Register to `ChannelRegistry`
4. Scan Skills extensions → Load into `SkillRegistry`

## API Endpoints

### Channel Configuration API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/channels` | List supported channel types |
| GET | `/api/channels/{type}/schema` | Get configuration form schema |
| GET | `/api/channels/{type}/connections` | List user's connections |
| POST | `/api/channels/{type}/connections` | Create connection |
| GET | `/api/channels/{type}/connections/{id}` | Get connection details |
| PATCH | `/api/channels/{type}/connections/{id}` | Update connection |
| DELETE | `/api/channels/{type}/connections/{id}` | Delete connection |
| POST | `/api/channels/{type}/connections/{id}/verify` | Verify connection |
| POST | `/api/channels/{type}/connections/{id}/enable` | Enable connection |
| POST | `/api/channels/{type}/connections/{id}/disable` | Disable connection |

### Channel Webhook API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/channel-hooks/{channel_type}/{connection_id}` | Receive platform push messages |

## Message Processing Flow

### Inbound Messages (Platform → Xuanwu)

1. User sends message on platform
2. Platform pushes to Xuanwu Webhook
3. `ChannelHandler.handle_inbound()` processes message
4. Verify signature, parse message, convert format
5. Route to corresponding Session
6. Agent executes and generates reply

### Outbound Messages (Xuanwu → Platform)

1. Agent generates `OutboundMessage`
2. `ChannelHandler.send_message()` sends message
3. Format conversion to platform format
4. Call platform API to send

## Implementation Priority

1. **Phase 1**: Auth Registry + Provider scanning mechanism
2. **Phase 2**: ChannelRegistry unified registry + ChannelHandler interface
3. **Phase 3**: Built-in channels migration to ChannelHandler
4. **Phase 4**: Channel Manager + Store + REST API
5. **Phase 5**: Feishu Handler (as Provider extension)
6. **Phase 6**: Slack/WhatsApp Handlers

## Extension Advantages

1. **Single Registry**: Use one `ChannelRegistry` to manage all channels, simplifying architecture
2. **Unified Interface**: Built-in and extension channels use the same `ChannelHandler` interface
3. **Simple and Flexible**: Auth, Channel, Skills each have independent interfaces
4. **Easy to Extend**: Add auth/channels/skills under providers directory
5. **Backward Compatible**: Skills existing scanning mechanism remains unchanged

---

*This design is based on current Xuanwu implementation, adopting unified Provider directory scanning mechanism and single ChannelRegistry architecture.*
