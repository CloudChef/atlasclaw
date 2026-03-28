# Channel Integrations Design

**Date:** 2026-03-11

## Goal

Design a unified channel integration architecture for Xuanwu that supports:

- user-managed channel connections
- multiple connections per user per channel type
- bidirectional chat integration
- incremental support for Feishu, Slack, WhatsApp, and future channels such as DingTalk

The first implementation target is Feishu as a chat channel only. Document, wiki, drive, and other business-tool capabilities are explicitly out of scope.

## Background

Xuanwu currently has:

- a core API layer for browser and client interaction under `app/xuanwu/api`
- a session and memory model already isolated by user
- a provider model for enterprise system capabilities under `service_providers`

It does not yet have:

- a unified user-owned channel connection model
- storage for per-user third-party chat channel credentials or runtime state
- a runtime manager for external chat connections
- a dedicated API for channel connection configuration

At the same time, the repo now includes imported OpenClaw channel extensions for:

- `extionsions/feishu`
- `extionsions/slack`
- `extionsions/whatsapp`

These extensions are useful as reference implementations, but they are not directly compatible with Xuanwu's current architecture.

## Scope

### In Scope

- unified channel connection model for multiple channel types
- file-based per-user channel configuration
- runtime management for user-owned channel connections
- inbound message routing from external channels into Xuanwu conversations
- outbound message delivery back to the originating channel connection
- frontend-facing REST APIs for connection management
- support for channel-specific webhook or websocket runtimes through per-channel drivers

### Out of Scope

- Feishu document, wiki, drive, permission, or chat management tools
- direct reuse of OpenClaw plugin registration or `plugin-sdk`
- merging channel webhooks into the existing markdown skill webhook dispatch system
- full implementation details for every supported channel in this design
- replacing Xuanwu's current browser/client websocket protocol

## Findings

### OpenClaw Extension Analysis

The imported OpenClaw extensions do not represent one uniform channel model.

#### Feishu

`extionsions/feishu` mixes several concerns:

- channel configuration and account resolution
- webhook and websocket event ingestion
- outbound message and typing support
- unrelated document/wiki/drive/permission tools

For Xuanwu, only the chat-channel subset is relevant.

#### Slack

`extionsions/slack` is relatively thin and depends heavily on OpenClaw's Slack plugin SDK. Its connection model is token-based and supports at least two transport modes:

- `socket`
- `http`

This means a generic channel architecture cannot assume webhook-only transport.

#### WhatsApp

`extionsions/whatsapp` uses a very different model based on local auth/session state and long-lived runtime listeners. It is not a simple credential + webhook integration.

### Xuanwu API Layer Analysis

The current `app/xuanwu/api` package divides responsibilities in a way that matters for this design.

#### Reusable

- `routes.py`
  - best place for frontend-facing connection CRUD APIs
  - suitable place to inject a new channel integration manager into `APIContext`
- `request_orchestrator.py`
  - the correct destination for normalized inbound chat messages
- `sse.py`
  - useful for async UI feedback if needed

#### Reference Only

- `gateway.py`
- `websocket.py`

These implement Xuanwu's own client protocol, not third-party chat channel transports. Their connection lifecycle ideas are useful, but their protocol and runtime cannot be reused directly for external channels.

#### Keep Separate

- `webhook_dispatch.py`

This is a markdown-skill dispatch subsystem authenticated by shared secret and targeted at backend skill execution. It should remain separate from chat-channel webhook handling.

## Design Principles

1. Channel integrations are not service providers.
2. User-owned channel connections must be isolated from platform-level static config.
3. Different channel types must share a common outer model but keep channel-specific inner config.
4. Channel runtimes must be pluggable because transport and auth models differ.
5. Xuanwu conversation orchestration should remain channel-agnostic.

## Recommended Architecture

The recommended architecture is a unified channel integration layer with three main pieces:

- `ChannelIntegrationStore`
- `ChannelIntegrationManager`
- `ChannelDriver`

### 1. ChannelIntegrationStore

Responsibilities:

- read and write per-user channel configuration files
- manage atomic updates and basic file locking
- expose CRUD operations on channel connections
- keep storage format versioned for future migration

This layer is storage-only. It must not own live runtime instances.

### 2. ChannelIntegrationManager

Responsibilities:

- load channel connections from the store
- create and stop running connection instances
- map inbound hook traffic to the correct connection
- delegate inbound parsing and outbound sending to channel drivers
- expose connection status for the API layer

This is the runtime owner for user channel connections.

### 3. ChannelDriver

Each channel type implements one driver. Examples:

- `FeishuDriver`
- `SlackDriver`
- `WhatsAppDriver`

Responsibilities:

- validate channel-specific config
- start and stop channel-specific runtimes
- handle inbound webhook or websocket events
- convert inbound traffic into Xuanwu message input
- send outbound replies, typing indicators, and media when supported
- expose channel-specific frontend form schema

## Proposed Module Layout

```text
app/xuanwu/channels/integrations/
  __init__.py
  models.py
  store.py
  manager.py
  registry.py
  hooks.py
  drivers/
    __init__.py
    base.py
    feishu.py
    slack.py
    whatsapp.py
```

Suggested API additions:

```text
app/xuanwu/api/channel_routes.py
app/xuanwu/api/channel_hooks.py
```

## Storage Model

### Directory Layout

Channel configuration should live in user-owned storage, not in `xuanwu.json`.

Recommended layout:

```text
~/.xuanwu/users/<user_id>/
  channels/
    feishu.json
    slack.json
    whatsapp.json
  sessions/
  memory/
```

### File Format

Each channel type uses one file per user.

Example:

```json
{
  "version": 1,
  "channel_type": "feishu",
  "updated_at": "2026-03-11T12:00:00Z",
  "connections": [
    {
      "id": "feishu_conn_001",
      "name": "work-bot",
      "enabled": true,
      "is_default": true,
      "created_at": "2026-03-11T12:00:00Z",
      "updated_at": "2026-03-11T12:00:00Z",
      "config": {},
      "runtime_state": {}
    }
  ]
}
```

### Common Connection Fields

- `id`
- `name`
- `enabled`
- `is_default`
- `created_at`
- `updated_at`
- `config`
- `runtime_state`

The outer structure is shared across all channels. The content of `config` is channel-specific.

### Channel-Specific Config Examples

#### Feishu

```json
{
  "app_id": "cli_xxx",
  "app_secret": "enc:xxxx",
  "verification_token": "enc:xxxx",
  "encrypt_key": "enc:xxxx",
  "mode": "webhook"
}
```

#### Slack

```json
{
  "mode": "http",
  "bot_token": "enc:xxxx",
  "app_token": "enc:xxxx",
  "signing_secret": "enc:xxxx",
  "user_token": "enc:xxxx"
}
```

#### WhatsApp

```json
{
  "auth_dir": "/path/to/auth",
  "phone_hint": "+86138xxxx0000"
}
```

### Runtime State

`runtime_state` is diagnostic and operational, not authoritative config.

Suggested fields:

- `status`
- `last_error`
- `last_seen_at`
- `last_verified_at`
- `hook_path`
- `login_state`

This state may be recomputed on startup.

## Driver Interface

Each `ChannelDriver` should implement a unified interface conceptually similar to:

- `channel_type()`
- `validate_config(config)`
- `describe_schema()`
- `start(connection, context)`
- `stop(connection_id)`
- `handle_hook(connection, request)`
- `send_message(connection, outbound)`
- `send_typing(connection, outbound)`
- `supports_threads()`
- `supports_media()`

This interface intentionally normalizes outer behavior without forcing all channels to share the same credential or transport model.

## Runtime and Message Flow

### Startup

On application startup:

1. initialize the channel integration store
2. initialize the channel driver registry
3. initialize the channel integration manager
4. load enabled user channel connections
5. start runtime instances for connections that support eager startup

### Inbound Flow

For an inbound event:

1. request hits a dedicated channel hook route
2. hook route resolves `channel_type` and `connection_id`
3. manager loads the matching connection record
4. matching driver validates the request and parses the event
5. driver returns a normalized inbound payload
6. payload is converted to Xuanwu message input
7. request is routed into `RequestOrchestrator`
8. model output is sent back using the same connection

### Outbound Flow

For an outbound reply:

1. orchestration completes for the inbound message
2. response bridge resolves the originating `connection_id`
3. manager looks up the active runtime handle
4. driver sends the message back to the external channel

This ensures replies go out through the same connection that received the inbound message.

## Hook and Transport Strategy

### Channel Hooks

Chat-channel hooks should be implemented as a new subsystem. They should not reuse `WebhookDispatchManager` directly.

Recommended route shape:

- `POST /api/channel-hooks/{channel_type}/{connection_id}`

Rationale:

- separates chat ingress from markdown-skill webhook dispatch
- allows per-connection routing without global credential guessing
- keeps hook semantics channel-specific

### Existing Webhook Dispatch

The current `webhook_dispatch.py` should remain unchanged in purpose:

- backend systems -> authenticated skill dispatch

The new channel hook subsystem should serve:

- external chat channels -> conversational message ingress

### Websocket-Based Channels

Some channels use persistent websocket or socket-based transports.

These should not reuse Xuanwu's existing API websocket/gateway runtime directly because:

- current websocket code serves Xuanwu's own client protocol
- third-party channel protocols are different
- auth and reconnect logic differ by platform

However, channel drivers may reuse similar lifecycle ideas internally:

- connection state
- heartbeat
- reconnect
- idempotent event handling

## API Design

### Configuration APIs

The frontend needs a uniform REST surface regardless of channel type.

Recommended endpoints:

- `GET /api/channels`
- `GET /api/channels/{channel_type}/connections`
- `POST /api/channels/{channel_type}/connections`
- `GET /api/channels/{channel_type}/connections/{connection_id}`
- `PATCH /api/channels/{channel_type}/connections/{connection_id}`
- `POST /api/channels/{channel_type}/connections/{connection_id}/verify`
- `POST /api/channels/{channel_type}/connections/{connection_id}/enable`
- `POST /api/channels/{channel_type}/connections/{connection_id}/disable`
- `DELETE /api/channels/{channel_type}/connections/{connection_id}`

### Schema APIs

To support Vue-based dynamic forms, expose schema metadata:

- `GET /api/channels/{channel_type}/schema`

Response should include:

- required fields
- field labels
- secrets vs plain text
- enum values
- channel capabilities

## Frontend Integration

The frontend should not hardcode Feishu-specific forms into the shared settings architecture.

Instead:

- the channel list page is shared
- each channel renders a driver-provided schema
- each user can manage multiple connections per channel type
- one connection per channel type may be marked default

The frontend flow should support:

1. choose channel type
2. create connection
3. fill channel-specific fields
4. validate connection
5. enable connection
6. show generated hook endpoint or login instructions

For secret fields:

- frontend submits them once
- backend stores encrypted values in the user file
- read APIs do not return raw secrets

## Integration with Existing API Layer

### `routes.py`

`routes.py` is the correct place to mount or include channel configuration APIs.

`APIContext` should be extended with:

- `channel_integration_store`
- `channel_integration_manager`
- `channel_driver_registry`

### `request_orchestrator.py`

Inbound channel messages should route into `RequestOrchestrator`.

The channel layer should attach extra context such as:

- `channel_type`
- `connection_id`
- `external_chat_id`
- `external_message_id`
- `reply_to_id`

### `gateway.py` and `websocket.py`

These should remain dedicated to Xuanwu browser/client communication.

No direct runtime reuse is recommended for external channel transports.

## Security Considerations

- secrets must not be stored in plaintext
- read APIs must redact secret fields
- each hook route must validate against the owning connection's config
- deleted or disabled connections must immediately stop accepting inbound events
- duplicate event delivery must be tolerated
- per-user channel config access must be enforced by authenticated user identity

## Feishu First-Phase Design

The first implementation target should be Feishu only, using the unified framework.

Phase 1 Feishu feature set:

- create and update user-owned Feishu bot connections
- validate config
- webhook-based inbound events
- text reply
- reply-to message
- typing indicator if practical in first phase

Excluded from phase 1:

- wiki/doc/drive/perm tools
- websocket mode
- advanced card streaming
- complex mention-forwarding semantics

## Risks

### 1. Runtime Diversity

Feishu, Slack, and WhatsApp have different transport models. A unified interface that is too narrow will not fit all channels; one that is too broad will become vague. The design therefore standardizes only the outer contract.

### 2. File Concurrency

Per-user JSON files are simple and align with current constraints, but concurrent writes and multi-process deployments will need careful locking and atomic replace behavior.

### 3. Secret Management

File-based config means encryption-at-rest and redacted API responses are mandatory.

### 4. Configuration Drift

`runtime_state` must not become a second source of truth. Persistent config and computed runtime state must remain clearly separated.

## Recommended Implementation Order

1. build unified models, store, registry, and manager
2. add channel configuration APIs
3. add dedicated channel hook routing
4. implement Feishu driver
5. bridge inbound Feishu messages into `RequestOrchestrator`
6. implement Feishu outbound replies
7. add tests for store, hooks, runtime, and outbound flow
8. add Slack and WhatsApp drivers on top of the same framework

## Decision Summary

Xuanwu should not port OpenClaw channel plugins directly.

Instead, it should introduce a unified channel integration subsystem with:

- per-user file-based channel config
- a shared connection record model
- a shared runtime manager
- per-channel drivers for transport and protocol specifics
- independent chat-channel hooks, separate from existing markdown-skill webhook dispatch

This design supports the immediate Feishu requirement while preserving a clean path for Slack, WhatsApp, DingTalk, and future channels.
