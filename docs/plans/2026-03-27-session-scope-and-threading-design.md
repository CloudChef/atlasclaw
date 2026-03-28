# Session Scope And Threading Design

## Background

Xuanwu currently has three related problems around conversation isolation:

1. Web chat cannot create a truly new conversation thread. The UI can reset or switch a
   stored `session_key`, but `POST /api/sessions` still returns a stable scope-derived
   session key, so "New Chat" does not create a new independent thread.
2. Session persistence is not physically isolated per authenticated user. The main
   `AgentRunner` uses a single `SessionManager(workspace_path=..., user_id="default")`,
   so session metadata and transcripts for all users are persisted under
   `workspace/users/default/sessions`.
3. Channel-originated traffic (Feishu, DingTalk, WeCom, other long connections) does
   not use the canonical `SessionKey` model. `ChannelManager` still builds ad-hoc
   session strings and does not enforce the same isolation rules as the web/API path.

These issues became more visible after:

- PR #25 added session list UI and `GET /api/sessions`
- PR #26 improved channel validation/runtime status, but still keeps channel session
  handling on the legacy path

## User Requirements

### Isolation rules

- Direct message conversations must be isolated per user.
- Group conversations must be shared per group.
- Different users from web chat, DingTalk, Feishu, and WeCom must never collide into
  the same session unless they intentionally share the same group conversation.

### Web chat product behavior

- "New Chat" must create a new independent conversation thread.
- `GET /api/sessions` must return all sessions visible to the current user, including
  sessions from all channels.

### Documentation

- Architecture, module details, development spec, and user-facing guidance must be
  updated to describe the final session/thread model.

## Review Findings

### Finding A: Session ownership is still not enforced on direct session endpoints

Current endpoints in `app/xuanwu/api/routes_session.py` accept an arbitrary
`session_key` and operate on it without verifying that the key belongs to the
authenticated user.

Affected endpoints:

- `GET /api/sessions/{session_key}`
- `POST /api/sessions/{session_key}/reset`
- `DELETE /api/sessions/{session_key}`
- `GET /api/sessions/{session_key}/status`
- `POST /api/sessions/{session_key}/queue`
- `POST /api/sessions/{session_key}/compact`

### Finding B: Session persistence still uses the global `default` storage bucket

`app/xuanwu/main.py` initializes the runtime session manager as:

```python
SessionManager(workspace_path=workspace_path, user_id="default", ...)
```

`app/xuanwu/agent/runner.py` then persists all transcripts via `self.sessions`.

This means user-specific `session_key.user_id` is currently metadata only. It does
not determine the actual storage location of session metadata or transcripts.

### Finding C: Channel session handling still bypasses canonical `SessionKey`

`app/xuanwu/channels/manager.py` still creates session identifiers using:

```python
session_key = f"channel:{channel_type}:{connection_id}:{message.chat_id}"
```

This string:

- does not use `SessionKey`
- does not encode `user_id`
- does not distinguish direct-message vs group-message semantics explicitly
- cannot participate cleanly in `GET /api/sessions` ownership filtering

PR #26 changed `deps.user_info` from anonymous to the channel connection owner's
`user_id`, which is an improvement for traceability, but it still does not solve
the session-key model mismatch.

### Finding D: Channel CRUD still uses placeholder user identification

`app/xuanwu/api/channels.py` still resolves current user by:

```python
return request.headers.get("X-User-Id", "default")
```

This bypasses the real auth state and is incompatible with strong per-user channel
ownership in production.

### Finding E: SPA "New Chat" still does not create a new thread

After PR #25, the SPA sidebar's "New Chat" button is only a link to `/`.
It does not call `startNewSession()` or any dedicated "create thread" API.

Even if it did, the current `createSession()` API still defaults to `scope: "main"`,
which would not produce a new independent thread.

## Design Goals

1. Keep `SessionKey` as the canonical conversation identity format.
2. Add first-class thread creation without breaking stable scope resolution.
3. Persist sessions and transcripts under each user's own workspace directory.
4. Unify web/API and channel session creation rules.
5. Make `GET /api/sessions` reliable across all channels.

## Chosen Solution

Use **Scheme B Enhanced**:

- Keep `SessionKey` as the canonical key model.
- Use `thread_id` to represent independent conversation threads.
- Introduce a session-manager resolver/factory so persistence is based on
  `SessionKey.user_id`, not the global `default` bucket.
- Migrate channel processing to canonical `SessionKey` construction.
- Add strict ownership checks for all direct session APIs.

## Target Session Model

### Canonical key fields

`SessionKey` remains:

- `agent_id`
- `user_id`
- `channel`
- `account_id`
- `chat_type`
- `peer_id`
- `thread_id`

### Semantics

- `user_id`: the authenticated principal inside Xuanwu
- `channel`: source channel (`web`, `feishu`, `dingtalk`, `wecom`, ...)
- `account_id`: channel connection/account namespace
- `chat_type`: `dm`, `group`, `channel`, or `thread`
- `peer_id`:
  - direct message: stable user/peer identifier
  - group: stable group/conversation identifier
- `thread_id`: independent conversation thread identifier within the same scope

## Scope Rules By Channel

### Web chat

- `channel = "web"`
- `chat_type = dm`
- `user_id = authenticated Xuanwu user`
- `peer_id = authenticated Xuanwu user`
- New conversation thread: generate a new `thread_id`

### Feishu

- Direct message:
  - `user_id = <xuanwu-principal-for-feishu-user>`
  - `chat_type = dm`
  - `peer_id = sender open_id`
- Group message:
  - `user_id = <xuanwu-principal-for-feishu-user>`
  - `chat_type = group`
  - `peer_id = chat_id`
- `account_id` should be the connection/account namespace, optionally enriched by
  tenant/app identity where available.

### DingTalk

- Direct message:
  - `user_id = <xuanwu-principal-for-dingtalk-user>`
  - `chat_type = dm`
  - `peer_id = sender_staff_id or sender_id`
- Group message:
  - `user_id = <xuanwu-principal-for-dingtalk-user>`
  - `chat_type = group`
  - `peer_id = conversation_id`
- `account_id` should identify the configured DingTalk connection.

### WeCom

- Direct message:
  - `chat_type = dm`
  - `peer_id = user identifier`
- Group message:
  - `chat_type = group`
  - `peer_id = chat/group identifier`

## API Changes

### Keep existing stable scope API

`POST /api/sessions`

- Purpose: create or fetch the default scope-derived session for compatibility.
- This endpoint should remain deterministic.

### Add explicit thread-creation API

`POST /api/sessions/threads`

Request:

- `agent_id`
- `channel`
- `chat_type`
- optional `account_id`
- optional `peer_id`

Behavior:

- derive authenticated `user_id`
- derive defaults for missing `peer_id`
- generate a fresh `thread_id`
- return a brand new `session_key`

### Expand session listing

`GET /api/sessions`

Behavior:

- return all sessions visible to the current user
- include sessions across all channels
- sort by last activity descending
- thread sessions and channel sessions must appear in the same response model

This requires the persistence layer to store sessions in per-user buckets and for
all channel-created sessions to use canonical `SessionKey`.

## Persistence Changes

### Problem

The current runtime writes all transcripts via one global `SessionManager`.

### Required change

Introduce a session manager resolver, for example:

- `SessionManagerFactory`
- or `SessionStorageRouter`

Responsibilities:

- parse `SessionKey.from_string(session_key)`
- choose the correct `user_id`
- return a `SessionManager(workspace_path=..., user_id=<parsed user>)`

Consumers to migrate:

- `AgentRunner`
- session API routes
- any code that persists or loads transcripts/metadata directly

### Expected storage layout

```text
workspace/
  users/
    <userId>/
      sessions/
        sessions.json
        <session_id>.jsonl
        <session_id>-topic-<thread_id>.jsonl
```

With this model:

- web/API user A and user B never share persisted session files
- `GET /api/sessions` for user A can read only user A's bucket
- channel sessions created for a user also live in that same user's bucket

## ChannelManager Changes

Replace the ad-hoc string session key with canonical `SessionKey`.

### Required metadata extraction

Channel handlers must provide enough structured metadata for `ChannelManager` to
derive:

- direct vs group semantics
- stable sender identity
- stable conversation/group identity
- connection/account namespace
- optional thread/topic identifier

### ChannelManager responsibilities

`ChannelManager` should:

1. map inbound message to a canonical channel principal
2. derive `chat_type`
3. derive `peer_id`
4. build `SessionKey`
5. route to `AgentRunner` using the canonical key

## Security Changes

### Direct session endpoint ownership enforcement

All direct session endpoints must verify:

```python
SessionKey.from_string(session_key).user_id == request.user.user_id
```

If not, return `404` or `403`.

### Channel API user resolution

`app/xuanwu/api/channels.py` must stop using the placeholder
`X-User-Id/default` logic and instead read the authenticated user from request state.

## Frontend Changes

### SPA new-chat behavior

Current behavior:

- sidebar "New Chat" only navigates to `/`

Target behavior:

- "New Chat" must call `POST /api/sessions/threads`
- store the returned `session_key`
- navigate to `/`
- refresh session list
- activate the new session immediately

### Session list behavior

The chat page should:

- load all sessions via `GET /api/sessions`
- show sessions across all channels
- allow switching by `session_key`
- preserve the active session in session storage

## Documentation To Update During Implementation

The implementation must update at least:

- `docs/architecture.md`
  - session identity model
  - per-user persistence
  - channel session routing
- `docs/module-details.md`
  - `SessionKey`
  - `SessionManager`
  - session API routes
  - `ChannelManager`
- `docs/development-spec.md`
  - security requirements for session ownership
  - channel/auth integration requirements
- `docs/GUIDE.md`
  - user-visible behavior for new chat, session switching, and cross-channel sessions
- `docs/Channel Guide.md`
  - channel handler obligations for session metadata extraction

## Implementation Order

1. Introduce session manager resolver/factory and migrate persistence off
   `user_id="default"` storage.
2. Add ownership checks to session APIs.
3. Add `POST /api/sessions/threads`.
4. Update SPA new-chat and session-switch behavior.
5. Migrate `ChannelManager` to canonical `SessionKey`.
6. Normalize Feishu/DingTalk/WeCom inbound metadata for scope derivation.
7. Update docs and tests.

## Acceptance Criteria

- Web chat "New Chat" creates a distinct `session_key` with a new `thread_id`.
- `GET /api/sessions` returns all sessions for the current user across all channels.
- Two different users do not share persisted session files.
- Direct-message sessions are isolated per user.
- Group-message sessions are shared per group, not per sender.
- Feishu, DingTalk, WeCom, and web all use canonical `SessionKey`.
- Session APIs reject cross-user `session_key` access.
