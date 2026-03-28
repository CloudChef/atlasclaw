# Session Scope And Threading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make session persistence and APIs correctly isolated per user across web and channel traffic, and add first-class thread creation for web chat.

**Architecture:** Keep `SessionKey` as the canonical routing identity, add a session-manager resolver that stores by parsed `user_id`, introduce explicit thread creation via `thread_id`, and migrate channel-originated traffic to the same canonical session model. Protect all direct session APIs with ownership checks and update the SPA to create and switch true thread sessions.

**Tech Stack:** FastAPI, Python async session/file persistence, Pydantic models, vanilla JS SPA frontend, pytest.

---

### Task 1: Add session manager resolver coverage

**Files:**
- Create: `app/xuanwu/session/router.py`
- Modify: `app/xuanwu/session/__init__.py`
- Test: `tests/xuanwu/session/test_session_manager_isolation.py`

- [ ] **Step 1: Write the failing tests**

Add tests that assert:
- two different `SessionKey.user_id` values route to different `sessions_dir`
- repeated resolution for the same `user_id` reuses the same directory logic
- legacy `user_id="default"` keys still resolve safely

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/session/test_session_manager_isolation.py -q -p no:cacheprovider`
Expected: FAIL because no resolver/factory exists yet.

- [ ] **Step 3: Write minimal implementation**

Create a resolver/factory that:
- accepts `workspace_path`
- parses `SessionKey.from_string(session_key)`
- returns `SessionManager(workspace_path=..., user_id=<parsed user>)`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/session/test_session_manager_isolation.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/xuanwu/session/router.py app/xuanwu/session/__init__.py tests/xuanwu/session/test_session_manager_isolation.py
git commit -m "test(session): add per-user session routing coverage"
```

### Task 2: Move runtime persistence off the global default bucket

**Files:**
- Modify: `app/xuanwu/agent/runner.py`
- Modify: `app/xuanwu/api/deps_context.py`
- Modify: `app/xuanwu/main.py`
- Test: `tests/xuanwu/test_agent_run_api.py`

- [ ] **Step 1: Write the failing test**

Add a test that runs the agent with a non-default `session_key.user_id` and verifies transcript/session metadata are written under that user's session directory, not `users/default/sessions`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/test_agent_run_api.py -q -p no:cacheprovider`
Expected: FAIL because `AgentRunner` still uses the startup-time `SessionManager(user_id="default")`.

- [ ] **Step 3: Write minimal implementation**

Update runtime persistence to resolve the correct `SessionManager` from `session_key` before:
- `get_or_create`
- `load_transcript`
- `mark_compacted`
- `persist_transcript`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/test_agent_run_api.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/xuanwu/agent/runner.py app/xuanwu/api/deps_context.py app/xuanwu/main.py tests/xuanwu/test_agent_run_api.py
git commit -m "fix(session): persist runtime transcripts by session user"
```

### Task 3: Enforce session ownership on direct session APIs

**Files:**
- Modify: `app/xuanwu/api/routes_session.py`
- Test: `tests/xuanwu/test_session_api_routes.py`

- [ ] **Step 1: Write the failing tests**

Add tests that verify a request from user A cannot:
- get user B's session
- reset user B's session
- delete user B's session
- inspect user B's session status

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/test_session_api_routes.py -q -p no:cacheprovider`
Expected: FAIL because ownership checks do not exist today.

- [ ] **Step 3: Write minimal implementation**

Implement a helper in `routes_session.py` that:
- parses `SessionKey.from_string(session_key)`
- compares against request user
- raises `404` or `403` on mismatch

Apply it to all direct session endpoints.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/test_session_api_routes.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/xuanwu/api/routes_session.py tests/xuanwu/test_session_api_routes.py
git commit -m "fix(api): enforce session ownership on direct session routes"
```

### Task 4: Add explicit thread session creation and aggregated session listing

**Files:**
- Modify: `app/xuanwu/api/schemas.py`
- Modify: `app/xuanwu/api/routes_session.py`
- Test: `tests/xuanwu/test_session_api_routes.py`

- [ ] **Step 1: Write the failing tests**

Add tests for:
- `POST /api/sessions/threads` returns distinct keys on repeated calls
- created keys include `thread_id`
- `GET /api/sessions` returns all sessions for the current user, including mixed channel/session keys in the same user bucket

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/test_session_api_routes.py -q -p no:cacheprovider`
Expected: FAIL because the thread endpoint and listing semantics do not exist.

- [ ] **Step 3: Write minimal implementation**

Add:
- request/response model for thread creation
- `POST /api/sessions/threads`
- `GET /api/sessions` backed by the current user's routed session manager

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/test_session_api_routes.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/xuanwu/api/schemas.py app/xuanwu/api/routes_session.py tests/xuanwu/test_session_api_routes.py
git commit -m "feat(session): add thread sessions and aggregated user listing"
```

### Task 5: Make SPA New Chat create a real new thread

**Files:**
- Modify: `app/frontend/scripts/api-client.js`
- Modify: `app/frontend/scripts/session-manager.js`
- Modify: `app/frontend/scripts/components/sidebar.js`
- Modify: `app/frontend/scripts/pages/chat.js`

- [ ] **Step 1: Write the failing test or reproduction note**

Document the current reproduction:
- open chat page
- click "New Chat"
- observe only route reload / same stable session

- [ ] **Step 2: Run targeted verification to confirm the bug**

Manual/automated check:
- create one session
- click "New Chat"
- verify `session_key` remains unchanged today

- [ ] **Step 3: Write minimal implementation**

Frontend changes:
- add `createThreadSession()` API call
- update `startNewSession()` to call the thread endpoint
- wire sidebar "New Chat" to thread creation rather than simple navigation
- refresh sidebar session list after creation

- [ ] **Step 4: Run verification to confirm it works**

Manual/automated check:
- click "New Chat"
- verify `session_key` changes and a new sidebar entry appears

- [ ] **Step 5: Commit**

```bash
git add app/frontend/scripts/api-client.js app/frontend/scripts/session-manager.js app/frontend/scripts/components/sidebar.js app/frontend/scripts/pages/chat.js
git commit -m "feat(frontend): create new chat threads from sidebar"
```

### Task 6: Canonicalize channel session creation

**Files:**
- Modify: `app/xuanwu/channels/manager.py`
- Modify: `app/xuanwu/channels/handlers/feishu.py`
- Modify: `app/xuanwu/channels/handlers/dingtalk.py`
- Modify: `app/xuanwu/channels/handlers/wecom.py`
- Modify: `app/xuanwu/api/channels.py`
- Test: `tests/xuanwu/test_channel_manager.py`

- [ ] **Step 1: Write the failing tests**

Add tests for:
- direct-message channel traffic produces canonical `SessionKey` values with sender-based isolation
- group traffic produces canonical `SessionKey` values keyed by group identifier
- channel management APIs use authenticated request user, not `"default"`

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/xuanwu/test_channel_manager.py -q -p no:cacheprovider`
Expected: FAIL because `ChannelManager` still creates ad-hoc channel session keys.

- [ ] **Step 3: Write minimal implementation**

Update channel flow to:
- derive authenticated user from real request state for channel CRUD
- normalize direct vs group metadata
- build canonical `SessionKey`
- carry `account_id`, `peer_id`, and `chat_type` consistently

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/xuanwu/test_channel_manager.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/xuanwu/channels/manager.py app/xuanwu/channels/handlers/feishu.py app/xuanwu/channels/handlers/dingtalk.py app/xuanwu/channels/handlers/wecom.py app/xuanwu/api/channels.py tests/xuanwu/test_channel_manager.py
git commit -m "fix(channel): unify channel sessions under SessionKey"
```

### Task 7: Update canonical documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/module-details.md`
- Modify: `docs/development-spec.md`
- Modify: `docs/GUIDE.md`
- Modify: `docs/Channel Guide.md`
- Modify: `docs/plans/2026-03-27-session-scope-and-threading-design.md`

- [ ] **Step 1: Update architecture docs**

Document:
- per-user session persistence
- thread session model
- cross-channel listing semantics

- [ ] **Step 2: Update module and development docs**

Document:
- `SessionKey` semantics
- ownership checks
- channel metadata requirements

- [ ] **Step 3: Update user and channel guides**

Document:
- New Chat behavior
- session switching
- group vs direct-message isolation

- [ ] **Step 4: Verify docs are internally consistent**

Check all updated docs for:
- matching terminology (`thread_id`, `peer_id`, `user_id`)
- no stale `users/default/sessions` claims

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md docs/module-details.md docs/development-spec.md docs/GUIDE.md "docs/Channel Guide.md" docs/plans/2026-03-27-session-scope-and-threading-design.md
git commit -m "docs(session): document threading and cross-channel isolation"
```

### Task 8: Full verification

**Files:**
- Modify as needed based on failures from verification

- [ ] **Step 1: Run focused UT suites**

Run:
- `pytest tests/xuanwu/test_session_api_routes.py -q -p no:cacheprovider`
- `pytest tests/xuanwu/test_agent_run_api.py -q -p no:cacheprovider`
- `pytest tests/xuanwu/test_channel_manager.py -q -p no:cacheprovider`

- [ ] **Step 2: Run full non-E2E test suite**

Run:
- `pytest tests/xuanwu -m "not e2e" -q -p no:cacheprovider`

- [ ] **Step 3: Run E2E suite**

Run:
- `pytest tests/xuanwu -m e2e -q -p no:cacheprovider`

- [ ] **Step 4: Verify service startup**

Run:
- `python -m uvicorn app.xuanwu.main:app --host 127.0.0.1 --port 8000`

Expected:
- service starts successfully
- `/api/health` returns `200`

- [ ] **Step 5: Commit final fixes**

```bash
git add .
git commit -m "test(session): verify threaded session isolation end to end"
```
