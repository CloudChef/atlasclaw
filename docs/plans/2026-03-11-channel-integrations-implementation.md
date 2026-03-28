# Channel Integrations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a unified user-owned channel integration framework in Xuanwu, then deliver the first production path for Feishu chat ingress and egress on top of it.

**Architecture:** Introduce a shared channel integration store, runtime manager, driver registry, and hook routing layer. Keep channel-specific protocol logic inside drivers, and bridge inbound events into the existing Xuanwu orchestration path without modifying provider semantics.

**Tech Stack:** FastAPI, Pydantic, file-based JSON storage, existing Xuanwu session/auth/orchestration modules, Feishu SDK or equivalent HTTP client

---

### Task 1: Add core integration models

**Files:**
- Create: `app/xuanwu/channels/integrations/models.py`
- Test: `tests/xuanwu/channels/test_channel_integration_models.py`

**Step 1: Write the failing test**

Write tests covering:

- connection record serialization
- channel file serialization
- secret redaction in API-facing payloads

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/channels/test_channel_integration_models.py -v`
Expected: FAIL because module or models do not exist

**Step 3: Write minimal implementation**

Implement:

- `ChannelConnectionRecord`
- `ChannelConnectionFile`
- `ChannelValidationResult`
- `ChannelRuntimeState`

Use explicit fields for shared metadata and plain `dict[str, Any]` for channel-specific `config`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/channels/test_channel_integration_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/models.py tests/xuanwu/channels/test_channel_integration_models.py
git -C xuanwu commit -m "feat: add channel integration models"
```

### Task 2: Add file-backed channel integration store

**Files:**
- Create: `app/xuanwu/channels/integrations/store.py`
- Test: `tests/xuanwu/channels/test_channel_integration_store.py`

**Step 1: Write the failing test**

Cover:

- creating an empty channel file
- loading a user/channel file
- upserting a connection
- deleting a connection
- atomic write behavior using temp file + replace

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/channels/test_channel_integration_store.py -v`
Expected: FAIL because store does not exist

**Step 3: Write minimal implementation**

Implement a store that:

- resolves per-user `channels/<channel_type>.json`
- reads and writes `ChannelConnectionFile`
- locks updates per process
- preserves `version` and `updated_at`

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/channels/test_channel_integration_store.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/store.py tests/xuanwu/channels/test_channel_integration_store.py
git -C xuanwu commit -m "feat: add channel integration store"
```

### Task 3: Add driver base contract and registry

**Files:**
- Create: `app/xuanwu/channels/integrations/drivers/base.py`
- Create: `app/xuanwu/channels/integrations/registry.py`
- Test: `tests/xuanwu/channels/test_channel_driver_registry.py`

**Step 1: Write the failing test**

Cover:

- registering a driver
- resolving a driver by `channel_type`
- rejecting duplicate registrations

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/channels/test_channel_driver_registry.py -v`
Expected: FAIL because registry does not exist

**Step 3: Write minimal implementation**

Implement:

- abstract `ChannelDriver`
- `ChannelDriverRegistry`

The base interface should include config validation, startup, shutdown, hook handling, outbound send, and schema description.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/channels/test_channel_driver_registry.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/drivers/base.py app/xuanwu/channels/integrations/registry.py tests/xuanwu/channels/test_channel_driver_registry.py
git -C xuanwu commit -m "feat: add channel driver registry"
```

### Task 4: Add runtime manager

**Files:**
- Create: `app/xuanwu/channels/integrations/manager.py`
- Test: `tests/xuanwu/channels/test_channel_integration_manager.py`

**Step 1: Write the failing test**

Cover:

- loading enabled connections
- starting and stopping connections
- resolving a connection by `channel_type` and `connection_id`
- updating runtime state

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/channels/test_channel_integration_manager.py -v`
Expected: FAIL because manager does not exist

**Step 3: Write minimal implementation**

Implement a manager that coordinates:

- store access
- driver registry lookup
- runtime handle lifecycle
- per-connection status tracking

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/channels/test_channel_integration_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/manager.py tests/xuanwu/channels/test_channel_integration_manager.py
git -C xuanwu commit -m "feat: add channel integration manager"
```

### Task 5: Add API context wiring

**Files:**
- Modify: `app/xuanwu/main.py`
- Modify: `app/xuanwu/api/routes.py`
- Test: `tests/xuanwu/test_channels.py`

**Step 1: Write the failing test**

Cover:

- `APIContext` includes channel integration manager and registry
- application startup initializes the new channel subsystem

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/test_channels.py -v`
Expected: FAIL because context wiring is missing

**Step 3: Write minimal implementation**

Update startup to:

- create store, driver registry, and manager
- register built-in drivers
- attach them to `APIContext`

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/test_channels.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/main.py app/xuanwu/api/routes.py tests/xuanwu/test_channels.py
git -C xuanwu commit -m "feat: wire channel integration runtime into api context"
```

### Task 6: Add channel configuration REST API

**Files:**
- Create: `app/xuanwu/api/channel_routes.py`
- Modify: `app/xuanwu/main.py`
- Test: `tests/xuanwu/api/test_channel_routes.py`

**Step 1: Write the failing test**

Cover:

- listing channel types
- listing user connections for a channel
- creating a connection
- updating a connection
- deleting a connection
- redacting secret fields in responses

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/api/test_channel_routes.py -v`
Expected: FAIL because routes do not exist

**Step 3: Write minimal implementation**

Expose routes under `/api/channels/...` and use authenticated `user_info.user_id` to scope all reads and writes.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/api/test_channel_routes.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/api/channel_routes.py app/xuanwu/main.py tests/xuanwu/api/test_channel_routes.py
git -C xuanwu commit -m "feat: add channel connection rest api"
```

### Task 7: Add independent channel hook routing

**Files:**
- Create: `app/xuanwu/api/channel_hooks.py`
- Modify: `app/xuanwu/main.py`
- Test: `tests/xuanwu/api/test_channel_hooks.py`

**Step 1: Write the failing test**

Cover:

- hook route resolves channel type and connection id
- disabled connection rejects hook traffic
- unknown connection returns not found
- manager delegates to driver hook handling

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/api/test_channel_hooks.py -v`
Expected: FAIL because channel hook routes do not exist

**Step 3: Write minimal implementation**

Create dedicated hook routes separate from `webhook_dispatch.py`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/api/test_channel_hooks.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/api/channel_hooks.py app/xuanwu/main.py tests/xuanwu/api/test_channel_hooks.py
git -C xuanwu commit -m "feat: add channel hook routing"
```

### Task 8: Add Feishu driver config and validation

**Files:**
- Create: `app/xuanwu/channels/integrations/drivers/feishu.py`
- Test: `tests/xuanwu/channels/test_feishu_driver.py`

**Step 1: Write the failing test**

Cover:

- Feishu schema description
- config validation for required credentials
- redaction of secret output fields

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/channels/test_feishu_driver.py -v`
Expected: FAIL because Feishu driver does not exist

**Step 3: Write minimal implementation**

Implement Feishu driver methods for:

- channel type declaration
- config schema
- config validation

Do not implement full hook handling yet.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/channels/test_feishu_driver.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/drivers/feishu.py tests/xuanwu/channels/test_feishu_driver.py
git -C xuanwu commit -m "feat: add feishu driver config validation"
```

### Task 9: Add Feishu inbound parsing bridge

**Files:**
- Modify: `app/xuanwu/channels/integrations/drivers/feishu.py`
- Modify: `app/xuanwu/api/channel_hooks.py`
- Test: `tests/xuanwu/channels/test_feishu_inbound.py`

**Step 1: Write the failing test**

Cover:

- webhook verification handling
- event payload to normalized Xuanwu inbound message conversion
- duplicate message tolerance

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/channels/test_feishu_inbound.py -v`
Expected: FAIL because Feishu inbound bridge is incomplete

**Step 3: Write minimal implementation**

Use the imported OpenClaw Feishu extension as reference only. Reimplement only the minimal chat ingress path needed for Xuanwu.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/channels/test_feishu_inbound.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/drivers/feishu.py app/xuanwu/api/channel_hooks.py tests/xuanwu/channels/test_feishu_inbound.py
git -C xuanwu commit -m "feat: add feishu inbound message bridge"
```

### Task 10: Connect inbound messages to request orchestration

**Files:**
- Modify: `app/xuanwu/channels/integrations/manager.py`
- Modify: `app/xuanwu/api/request_orchestrator.py`
- Test: `tests/xuanwu/api/test_channel_orchestration.py`

**Step 1: Write the failing test**

Cover:

- inbound Feishu message reaches `RequestOrchestrator`
- user identity and extra context include connection metadata
- session key is stable across repeated messages from the same external thread

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/api/test_channel_orchestration.py -v`
Expected: FAIL because channel-orchestration bridge is missing

**Step 3: Write minimal implementation**

Bridge normalized inbound channel events into orchestrator execution and preserve origin metadata for outbound routing.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/api/test_channel_orchestration.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/manager.py app/xuanwu/api/request_orchestrator.py tests/xuanwu/api/test_channel_orchestration.py
git -C xuanwu commit -m "feat: bridge channel ingress to request orchestration"
```

### Task 11: Add Feishu outbound reply path

**Files:**
- Modify: `app/xuanwu/channels/integrations/drivers/feishu.py`
- Modify: `app/xuanwu/channels/integrations/manager.py`
- Test: `tests/xuanwu/channels/test_feishu_outbound.py`

**Step 1: Write the failing test**

Cover:

- reply to original conversation
- reply to specific message when metadata exists
- typing support when enabled

**Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/channels/test_feishu_outbound.py -v`
Expected: FAIL because outbound bridge is incomplete

**Step 3: Write minimal implementation**

Implement text send, reply-to send, and optional typing indicator through the Feishu driver.

**Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/channels/test_feishu_outbound.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git -C xuanwu add app/xuanwu/channels/integrations/drivers/feishu.py app/xuanwu/channels/integrations/manager.py tests/xuanwu/channels/test_feishu_outbound.py
git -C xuanwu commit -m "feat: add feishu outbound reply flow"
```

### Task 12: Add documentation and configuration examples

**Files:**
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/PROJECT_OVERVIEW.md`
- Modify: `docs/FILE-STRUCTURE.MD`
- Modify: `docs/PROVIDER-GUIDE.MD`
- Test: none

**Step 1: Write the documentation changes**

Document:

- new channel integration storage layout
- separation between service providers and user-owned channels
- Feishu first-phase support

**Step 2: Review docs for consistency**

Run: `rg -n "channel integration|Feishu|service_providers|channel-hooks" docs`
Expected: updated references appear in the right docs

**Step 3: Commit**

```bash
git -C xuanwu add docs/DEPLOYMENT.md docs/PROJECT_OVERVIEW.md docs/FILE-STRUCTURE.MD docs/PROVIDER-GUIDE.MD
git -C xuanwu commit -m "docs: document channel integration architecture"
```

### Task 13: Run verification suite

**Files:**
- Modify: none
- Test: all touched test files

**Step 1: Run focused tests**

Run:

```bash
pytest tests/xuanwu/channels -v
pytest tests/xuanwu/api/test_channel_routes.py -v
pytest tests/xuanwu/api/test_channel_hooks.py -v
pytest tests/xuanwu/api/test_channel_orchestration.py -v
```

Expected: PASS

**Step 2: Run broader regression tests**

Run:

```bash
pytest tests/xuanwu/test_channels.py -v
pytest tests/xuanwu -q
```

Expected: PASS or known unrelated failures documented explicitly

**Step 3: Commit if only test or doc touch-ups were needed**

```bash
git -C xuanwu add -A
git -C xuanwu commit -m "test: finalize channel integration verification"
```

Plan complete and saved to `docs/plans/2026-03-11-channel-integrations-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
