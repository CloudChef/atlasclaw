# AtlasClaw Provider Extension Standard (v1)

Status: Draft for review
Applies to: AtlasClaw core, all provider packages
Companion documents: `docs/PROVIDER-GUIDE.MD`, `<providers-repo>/docs/plans/2026-11-24-audit-report-provider-design.md`

---

## 1. Purpose

Define one **stable, versioned contract** for provider packages to embed:

- **UI extensions** — navigation entries, pages, settings panels, widgets.
- **Backend extensions** — provider-owned HTTP APIs, background jobs, events.
- **Host services** — the narrow interface core exposes to provider code.

The standard is designed so that new capabilities can be added by **extending the manifest and adding extension points**, without changing the shape of existing providers. Unknown fields are ignored; unknown extension points are skipped with a warning. Existing providers (jira, SmartCMP, markdown-vault) keep working unchanged.

---

## 2. Terminology

| Term | Meaning |
|---|---|
| Provider package | Directory under `providers_root` containing `PROVIDER.md` |
| Provider type | Stable identifier (`provider_type`), `^[a-z0-9][a-z0-9-]{1,63}$` |
| Instance | One configured deployment of a provider type; extension-only providers get an implicit `default` instance |
| Extension point | A declared integration surface: `ui`, `api`, `jobs`, `events`, `settings` |
| Host | AtlasClaw core process that discovers and runs provider code |
| Host API version | Version of the Python host-services interface (`HOST_API_VERSION`) |
| Extension manifest version | Version of the manifest contract (`extensions.version`) |

---

## 3. Package layout

```
providers/<provider-type>/
  PROVIDER.md                 # required, LLM-facing contract
  provider.schema.json        # required for any extension point
  runtime-api.json            # required when extensions.api is declared
  README.md                   # human-facing
  assets/                     # icons/images (served as provider assets)
  ui/                         # required when extensions.ui is declared
    index.html
    ...
  api/                        # handler modules referenced by runtime-api.json
    __init__.py
    handlers.py
  settings.schema.json        # optional, reserved for extensions.settings
  skills/                     # provider-owned skills
  docs/                       # provider design notes
  test/                       # provider tests
```

Rules:

- `PROVIDER.md`, `provider.schema.json`, `runtime-api.json`, `ui/`, `api/` names are fixed.
- All declared paths are **relative to the provider root**, POSIX separators, no `..`, no absolute paths, no symlinks.
- Installed providers run in the AtlasClaw Python process and share its installed dependencies. Providers use Host Services for Core functionality. Core internals are outside the provider contract.
- Assets must not leave the provider root; core rejects traversal and symlinks.

---

## 4. Manifest: `provider.schema.json`

### 4.1 Top-level shape

```json
{
  "schema_version": 1,
  "provider_type": "example",
  "catalog": {
    "display_name": "Example",
    "description": "…",
    "badge": "EX",
    "icon": "EX",
    "icon_path": "assets/icon.svg",
    "accent": "#1d4ed8",
    "extension_only": false
  },
  "runtime_capabilities": ["documents"],
  "config_schema": { "fields": [] },
  "extensions": {
    "version": 1,
    "requires": { "host": ">=1.0 <2.0", "extensions": [1] }
  }
}
```

`extensions` is optional. A provider without it behaves exactly as today.

### 4.2 `extensions` block (v1)

```json
"extensions": {
  "version": 1,
  "requires": { "host": ">=1.0 <2.0" },
  "ui": {
    "assets_root": "ui",
    "nav": [
      {
        "id": "file-audit",
        "title": { "zh-CN": "文件审核", "en-US": "File Audit" },
        "i18n_key": "ext.example.fileAudit",
        "icon_path": "assets/icon.svg",
        "entry": "index.html",
        "access": "user",
        "permissions": [],
        "layout": "full",
        "order": 10
      }
    ]
  },
  "api": {
    "manifest": "runtime-api.json",
    "timeout_seconds": 30,
    "max_body_bytes": 33554432
  }
}
```

Field rules:

| Field | Type | Rules |
|---|---|---|
| `version` | int | Manifest contract version. Core accepts `1..N`; higher versions are skipped with a warning |
| `requires.host` | string | Declared compatibility range, reserved for future enforcement |
| `ui.assets_root` | string | Directory name under provider root. Default `ui` |
| `ui.nav[].id` | string | `^[a-z0-9][a-z0-9-]{0,63}$`; unique per provider |
| `ui.nav[].title` | object | Locale → text. At least one locale required unless `i18n_key` is set |
| `ui.nav[].entry` | string | Relative HTML file under `assets_root` |
| `ui.nav[].access` | enum | `user` (default) or `admin` |
| `ui.nav[].permissions` | string[] | Reserved; RBAC module permissions, e.g. `documents.view` |
| `ui.nav[].layout` | enum | `full` (default, no padding) or `padded` |
| `ui.nav[].order` | int | Ascending; ties sorted by `provider_type`, then `id` |
| `api.manifest` | string | Discovery currently uses the fixed name `runtime-api.json` |
| `api.timeout_seconds` | int | Descriptive. Set the HTTP default in `runtime-api.json` |
| `api.max_body_bytes` | int | Descriptive. Set the HTTP default in `runtime-api.json` |

Reserved (accepted, ignored in v1, reserved for future versions):

- `extensions.widgets[]` — embeddable panels for chat or admin pages
- `extensions.settings` — provider settings page manifest
- `extensions.jobs` — declarative job types
- `extensions.events` — published/subscribed event topics
- `extensions.commands[]` — slash commands contributed by the provider

Unknown fields anywhere in the manifest are ignored. Unknown `ui.nav[].layout` values fall back to `full`.

### 4.3 Identity and namespacing

- All extension identifiers are namespaced as `<provider_type>.<id>` internally (e.g. `audit-report.file-audit`).
- Routes are generated by core; providers never choose global routes:
  - UI page: `/ext/<provider_type>/<nav_id>`
  - Assets: `/api/ui/extensions/<provider_type>/<nav_id>/<path>`
  - API: `/api/providers/<provider_type>/<instance>/<path>`
- Two providers cannot collide because the namespace is the provider type.

---

### 4.4 Provider-owned skills

Skills under `skills/` retain the provider namespace and are addressed as
`provider:skill`. This canonical identity controls Provider ownership,
permissions, and inherited model policy. A Provider-owned Skill that uses the
**standard skill runtime** for local scripts declares:

```yaml
execution: standard
```

in `SKILL.md` frontmatter. This selects the local standard execution runtime; it
does not remove Provider ownership or change the `provider:skill` identity.
When selected, a docs-only standard Skill can receive the standard
`skill_read` / `skill_write` / `skill_exec` tools. Skills loaded from the
standalone Skill directories keep their bare-name identities.



### 5.1 Discovery

Core aggregates every provider's `extensions.ui.nav` at startup and exposes:

```
GET /api/ui/extensions
→ {
  "host": { "version": 1, "base_path": "/atlasclaw", "locale": "zh-CN", "theme": "light" },
  "items": [
    {
      "id": "audit-report.file-audit",
      "provider_type": "audit-report",
      "nav_id": "file-audit",
      "title": "文件审核",
      "route": "/ext/audit-report/file-audit",
      "entry_url": "/api/ui/extensions/audit-report/file-audit/",
      "access": "user",
      "order": 10
    }
  ]
}
```

- Listing and page access require authentication. `access=admin` also requires an administrator. Provider API routes perform their own authorization.
- `entry_url` is absolute from the site root (includes reverse-proxy prefix).
- The endpoint is safe to call from the SPA on every startup; results are cheap and cacheable for 60 s.

### 5.2 Navigation and routing

- Core renders provider nav entries in the **left sidebar**, below the primary actions, in `order` sequence.
- Clicking navigates to the generated `route`; the SPA route pattern is `/ext/:providerType/:navId`.
- Direct browser navigation to the route must work (deep link, refresh).
- Unauthorized users do not see the entry; direct access renders a 403 panel.
- Core may re-render nav after login/logout or permission changes.

### 5.3 Assets

```
GET /api/ui/extensions/{provider_type}/{nav_id}/{path}
```

- `/` and empty path map to the declared `entry`.
- Only files under `assets_root` are served. Path traversal, symlinks, and NUL bytes → `404`.
- MIME allowlist: `html, js, mjs, css, json, svg, png, jpg, jpeg, webp, gif, ico, woff2, txt, map`.
- Headers:
  - HTML: `Cache-Control: no-store`
  - Others: `Cache-Control: private, max-age=60`
  - Always: `X-Content-Type-Options: nosniff`
  - HTML also: `Content-Security-Policy: frame-ancestors 'self'`
- Provider pages must reference assets **relatively** so the reverse-proxy prefix keeps working.

### 5.4 iframe host

Core renders the page inside an iframe:

```html
<iframe
  src="/api/ui/extensions/audit-report/file-audit/"
  sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
  referrerpolicy="same-origin"
  style="border:0;width:100%;height:100%"
></iframe>
```

- Same-origin iframe is required for cookie-authenticated API calls.
- Core listens for bridge messages and validates `event.origin === window.location.origin` and `event.source === iframe.contentWindow`.
- If the page does not send `atlasclaw:ready` within 5 s, core shows a load-failure panel with the provider name and a retry action.

### 5.5 Bridge protocol (v1)

Parent → iframe:

```js
{ type: "atlasclaw:init", version: 1,
  locale: "zh-CN", theme: "light", basePath: "/atlasclaw",
  provider: { type: "audit-report", navId: "file-audit" },
  user: { id: "u-1", displayName: "…", isAdmin: false, roles: ["user"] } }

{ type: "atlasclaw:locale", locale: "en-US" }
{ type: "atlasclaw:theme", theme: "dark" }
```

Iframe → parent:

| Message | Payload | Effect |
|---|---|---|
| `atlasclaw:ready` | `{version:1}` | Hide loader |
| `atlasclaw:title` | `{title}` | Update page title |
| `atlasclaw:toast` | `{level:"info"\|"success"\|"error", message}` | Show core toast |
| `atlasclaw:navigate` | `{route}` | Navigate SPA (allowed internal routes only) |
| `atlasclaw:resize` | `{height}` | Optional; ignored when core uses full-height layout |
| `atlasclaw:close` | `{}` | Navigate back to chat |

Rules:

- Unknown message types are ignored (forward compatible).
- Providers must not post messages before receiving `atlasclaw:init`.
- Bridge is UI-only. Data access uses same-origin `fetch` with cookies.
- Providers must use `basePath` for links/images when they cannot use relative URLs.

### 5.6 UI conventions

- **i18n**: read `locale` from init; fall back to `en-US`.
- **Theme**: read `theme` from init; use core CSS variables when `padded`; full-page providers own their styling.
- **Errors**: human-readable message + machine code; never surface stack traces.
- **Loading**: first paint within 1 s; skeleton or spinner for longer operations.
- **Files**: uploads use `multipart/form-data`; downloads use provider API `Content-Disposition`.
- **Accessibility**: keyboard reachable controls, focus ring, `aria-label` on icon-only buttons.
- **Deep links**: internal state may use hash routes (`#/documents/123`).

---

## 6. Backend embedding contract

### 6.1 `runtime-api.json` (v1)

```json
{
  "version": 1,
  "routes": [
    {
      "id": "documents.list",
      "method": "GET",
      "path": "documents",
      "module": "api/handlers.py",
      "handler": "list_documents",
      "capability": "documents",
      "access": "user",
      "success_status": 200,
      "response": "json",
      "timeout_seconds": 30,
      "max_body_bytes": 1048576
    },
    {
      "id": "documents.file",
      "method": "GET",
      "path": "documents/{document_id}/file",
      "module": "api/handlers.py",
      "handler": "download_document",
      "capability": "documents",
      "request_method": "GET",
      "response": "binary"
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | no | Stable identifier for logs/tests; defaults to `<method> <path>` |
| `method` | yes | `GET/POST/PUT/PATCH/DELETE` |
| `path` | yes | Relative, no leading slash, `{param}` segments allowed |
| `module` | yes | Safe relative `.py` path under provider root |
| `handler` | yes | Async function name |
| `capability` | yes | Must be declared in `provider.schema.json → runtime_capabilities` |
| `access` | no | `user` (default) or `admin` |
| `success_status` | no | Default 200 |
| `response` | no | `json` (default) or `binary` |
| `timeout_seconds` | no | Overrides the top-level `runtime-api.json` default (30 s) |
| `max_body_bytes` | no | Overrides the top-level `runtime-api.json` default (32 MiB), up to 256 MiB |

Declare `version: 1` (legacy alias `schema_version` is accepted). The remaining optional fields default as listed above.

### 6.2 Handler contract

```python
async def handler(request, context) -> dict | Response:
    ...
```

- `request`: Starlette `Request` with a replayable, size-limited body.
- `context`: `ProviderHttpContext` (stable fields):
  - `provider_type`, `provider_instance`, `provider_config`
  - `path_params: dict[str, str]`
  - `user_id`, `tenant_id`, `is_admin`, `token_id`
  - `host`: Host Services v1 (see 6.4)
- Return:
  - `dict` / list → JSON (default)
  - A materialized `Response` on a route with `response: "binary"` serves files and HTML. The provider sets `Content-Type` and `Content-Disposition`. Core supplies `nosniff` and a sandbox CSP for HTML if those headers are absent. Streaming responses are unsupported.
- To report an expected failure, raise a provider-local exception with
  `status_code`, `code`, and `detail` attributes.

```python
raise ProviderError(
    "Document not found", status_code=404,
    code="document_not_found",
)
```

Error envelope returned to clients:

```json
{ "detail": { "code": "document_not_found", "message": "Document not found" } }
```

#### Optional visual bridge

Provider HTTP handlers may use the Core-managed visual bridge on
`ProviderHttpContext` when `context.visual_available` is true. The bridge accepts
the legacy single-image arguments or an ordered `images` list:

```python
# Legacy single image
text = await context.run_visual(
    prompt="Describe visible fields on this page.",
    system_prompt="Describe only observable image content.",
    timeout_seconds=60,
    data=image_bytes,
    media_type="image/png",
    identifier="page-12",
)

# Multiple images in one model turn
text = await context.run_visual(
    prompt="Describe the visible content in order.",
    system_prompt="Describe only observable image content.",
    timeout_seconds=60,
    images=[
        {"data": image_bytes, "media_type": "image/png", "identifier": "page-12"},
        {"data": next_image_bytes, "media_type": "image/png", "identifier": "page-13"},
    ],
)
```

Each image identifier is also sent as a text label immediately before its image,
preserving the supplied order. Providers validate supported formats and input
limits. Core resolves the authorized Provider/Skill model policy and applies that
model's explicit settings;
configured `temperature` and `max_tokens` are passed through. A truncated, filtered,
or errored model response raises a technical failure; partial text is not returned
as a successful result.

#### Optional text model bridge

Provider HTTP handlers that implement a provider-defined text stage may
call the Core-managed bridge on `ProviderHttpContext`:

```python
result = await context.run_text(
    prompt="Perform this one text-processing stage.",
    system_prompt="Follow the provider-supplied stage contract.",
    skill_id="provider-qualified-skill-id",  # optional registered Skill of this Provider
)
```

Core resolves the current global binding for the authorized Provider instance and
optional canonical `skill_id`; the same optional identity is accepted by
`run_visual`. A supplied Skill must belong to the trusted Provider and remain
available under the current actor's grants. Without `skill_id`, the Provider
default policy applies. Credentials are resolved afresh and are never returned to
the Provider. By default, this bridge adds no request deadline or
output-token setting; callers may pass an explicit `timeout_seconds` when needed.
This bridge runs one direct text model operation; it
does not enter `ProviderAgentHost.start_run`, chat capability selection, or tool
routing. It does not expose credentials. Empty input, unavailable models,
execution errors, empty output, and incomplete model responses fail as
technical errors; partial output is never returned as success. Ordinary
`ProviderAgentHost.start_run` uses the same global model-policy resolver while
retaining its existing skill/tool execution scopes. Provider code cannot use
caller-supplied model IDs or Tokens to escape that policy.

### 6.3 Limits and lifecycle

| Item | Default | Notes |
|---|---|---|
| Request body | 32 MiB | Manifest and route overrides up to 256 MiB |
| Handler timeout | 30 s | Long work must be a job |
| Response size | Materialized in memory | Apply a provider-specific file size limit |
| Route discovery | startup only | Restart to reload; invalid package fails closed |

### 6.4 Host Services (v1)

Providers receive a narrow host object; they do **not** import core internals.

```python
class ProviderHost(Protocol):
    api_version: str                      # "1.0"
    base_path: str
    workspace_path: Path                  # user workspace root
    data_dir: Path                        # provider-private data dir (per user)
    shared_data_dir: Path                 # shared by all users/tenants of this provider
    user: ProviderUser                    # id, tenant_id, is_admin, locale, roles
    agent: ProviderAgentHost              # first-class agent invocation, see 6.5

    def get_config(self, key: str, default: Any = None) -> Any: ...
    async def emit_event(self, topic: str, payload: dict) -> None: ...
```

Rules:

- Host Services is additive across minor versions; fields are never removed within a major version.
- `data_dir` is created by core on first use and isolated per `provider_type`/tenant/user.
- Use `shared_data_dir` for data deliberately shared across users and tenants. The provider creates its subdirectories and controls API permissions for shared reads and writes.
- Providers never construct `UserInfo`, `APIContext`, or a `SkillDeps`; they pass intent to `host.agent`.

### 6.5 Agent invocation contract

Provider backends are allowed — and expected — to run agent turns, because capabilities such as `audit-report` are implemented by a skill that the agent executes. This is a **first-class contract**, not a workaround.

#### 6.5.1 Interface

```python
class ProviderAgentHost(Protocol):
    api_version: str  # "1.0"

    async def run_skill(
        self,
        *,
        skill_id: str,                       # e.g. "audit-report"
        prompt: str,
        files: Sequence[ProviderFileRef] = (),
        session: ProviderSessionSpec | None = None,
        timeout_seconds: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ProviderRunHandle: ...

    async def start_run(
        self,
        *,
        prompt: str,
        skills: Sequence[str] = (),          # explicit allowlist/pinning
        tools: Sequence[str] = (),
        files: Sequence[ProviderFileRef] = (),
        session: ProviderSessionSpec | None = None,
        model_token_id: str | None = None,
        timeout_seconds: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ProviderRunHandle: ...

    async def get_run(self, run_id: str) -> ProviderRunSnapshot: ...
    def stream_run(self, run_id: str) -> AsyncIterator[ProviderRunEvent]: ...
    async def cancel_run(self, run_id: str, reason: str = "") -> None: ...
    async def list_artifacts(self, run_id: str) -> list[ProviderArtifact]: ...
    async def collect(self, paths: Sequence[Path]) -> list[ProviderArtifact]: ...
```

#### 6.5.2 Skill pinning and determinism

- `run_skill(skill_id=...)` is sugar for `start_run(skills=[skill_id])`.
- When `skills` is non-empty, routing **must select one of those skills**. It must not silently fall back to another skill, a generic tool, or a direct answer.
- If the skill is unknown, disabled by RBAC, or not loaded, the run fails immediately with `skill_unavailable` (HTTP equivalent 409 for synchronous start).
- The `tools` argument can narrow tool targets for a pinned skill. Standalone
  tool pinning and `model_token_id` selection are not implemented in v1.
- When both are empty, normal capability routing selects from the skills and tools available to the authenticated user. This includes the provider's own skill when the user has enabled it. Routing selects relevant capabilities for the turn.

Use pinning when a workflow requires a specific skill. A provider that performs
its own orchestration can use normal user-scoped routing.

#### 6.5.3 Execution context

- The run executes **as the authenticated user** from `context.host.user`; RBAC, skill permissions, and provider permissions are applied exactly as for a UI request.
- Providers cannot raise privileges, impersonate another user, or bypass skill allowlists.
- Skills execute with the run's work directory; `skill_exec` cwd and path jail are unchanged from the UI path.
- `metadata` is echoed into the run snapshot and events, so providers can correlate `document_id`/job ids.

#### 6.5.4 Files and artifacts

`ProviderFileRef`:

```python
@dataclass(frozen=True)
class ProviderFileRef:
    path: Path            # inside provider data_dir or workspace; validated by host
    name: str             # display name
    mime: str = ""        # optional
```

- Files are copied by core into the run work dir under `uploads/provider/<run_id>/`; the agent sees ordinary paths.
- Artifacts are collected from two sources:
  1. explicit `skill_exec(... download_paths=[...])` results (existing workspace download mechanism);
  2. `ProviderAgentHost.collect(paths=[...])` for files the provider knows it needs (optional).
- `ProviderArtifact`:

```python
@dataclass(frozen=True)
class ProviderArtifact:
    name: str
    path: Path            # copied into provider data_dir by the host
    mime: str
    size: int
    source: str           # "download_path" | "explicit"
```

- The host copies artifacts into `host.data_dir/runs/<run_id>/`; the provider owns retention afterwards.

#### 6.5.5 Sessions

```python
@dataclass(frozen=True)
class ProviderSessionSpec:
    kind: str = "ephemeral"   # "ephemeral" | "persistent"
    key: str = ""             # required for persistent; namespaced by core
    retain: bool = False      # keep transcript after the run
```

- `ephemeral` (default): core creates a hidden session
  `agent:main:user:<user_id>:provider-<provider_type>-<run_id>`, removes it after
  the run unless `retain=true`.
- `persistent`: provider supplies a key; core prefixes it with `provider-<provider_type>-` and rejects keys that collide with user chat sessions.
- Sessions under the `provider-` scope are execution plumbing: `GET /api/sessions`
  filters them out, so provider runs never appear as user conversations in the
  chat sidebar, even if a cleanup is interrupted.
- Providers cannot read or write transcripts outside their namespace.

#### 6.5.6 Events and progress

`ProviderRunHandle` exposes:

```python
run_id: str
session_key: str

async def wait(self, timeout: float | None = None) -> ProviderRunSnapshot: ...
def events(self) -> AsyncIterator[ProviderRunEvent]: ...   # replay + live
```

`ProviderRunEvent`:

```json
{ "type": "run_started|phase|assistant_delta|tool_started|tool_finished|artifact|run_finished|run_failed",
  "run_id": "…",
  "seq": 12,
  "at": "2026-11-24T10:35:12Z",
  "data": { } }
```

- Events are replayable from `seq=0` while the provider job lives.
- Standard phases: `planning → executing → finalizing`; providers may map phases to percentages.
- `assistant_delta` carries incremental text for progress UI.
- `tool_started/tool_finished` carry tool name and a redacted summary (never raw file bytes or secrets).
- `run_finished` includes `final_answer`, `usage`, and `artifacts`.
- `run_failed` includes `code` and `message`.

`ProviderRunSnapshot`:

```json
{
  "run_id": "…",
  "status": "queued|running|succeeded|failed|cancelled",
  "phase": "executing",
  "progress": 42,
  "message": "Extracting report",
  "final_answer": "审核完成：…",
  "error": { "code": "skill_unavailable", "message": "…" },
  "skill_ids": ["audit-report"],
  "artifacts": [ { "name": "R2026001_审核报告.html", "path": "runs/…/report.html", "mime": "text/html", "size": 441974 } ],
  "usage": { "input_tokens": 0, "output_tokens": 0 },
  "session_key": "…",
  "started_at": "…",
  "finished_at": "…",
  "metadata": { "document_id": "…" }
}
```

#### 6.5.7 Limits, cancellation, failure

| Item | Default |
|---|---|
| Event buffer | last 500 events per run, replay retained while the job exists |
| Restart behavior | on core restart, unfinished provider runs are marked `failed` / `host_restarted` |
| Cancellation | cooperative abort signal; status `cancelled`; partial artifacts retained |

Error codes (stable):

- `skill_unavailable`
- `skill_permission_denied`
- `run_timeout`
- `run_cancelled`
- `run_failed`
- `artifact_copy_failed`
- `host_restarted`

#### 6.5.8 Reference usage (audit-report)

```python
handle = await context.host.agent.run_skill(
    skill_id="audit-report",
    prompt=f"请使用 audit-report 审核 {file_ref.name}，生成完整 HTML 报告",
    files=[file_ref],
    session=ProviderSessionSpec(kind="ephemeral"),
    timeout_seconds=1200,
    metadata={"document_id": document_id},
)
run_id = handle.run_id
```

The provider stores `run_id`, forwards `events()` into its own job record (status/progress), and on `run_finished` copies `artifacts[0]` into its data dir and marks the document `completed`.

### 6.6 Jobs and events (reserved, v2)

Declared as `extensions.jobs` / `extensions.events`; core will provide:

- Job states: `queued → running → succeeded | failed | cancelled`
- Standard job record: `id, type, status, progress, message, result, error_code, created_at, updated_at`
- Event bus topics namespaced by provider.

Providers may ship their own job tables in `data_dir` in v1; they must expose the same status vocabulary so UI code stays portable.

---

## 7. Permissions and security

| Surface | Rule |
|---|---|
| Manifest | Admin-installed packages are trusted; invalid manifests are skipped, not fatal |
| Nav visibility | Authenticated users see `access=user`; `access=admin` requires admin |
| Provider API | Authenticated; `access=admin` requires admin; document/file handlers must re-check ownership |
| Assets | Path jail under `assets_root`; no symlinks; MIME allowlist |
| HTML | `CSP: sandbox` when served inline; `frame-ancestors 'self'` for pages |
| iframe | Sandbox flags above; origin-checked bridge |
| Secrets | Provider config stays in core config store; never embedded in UI assets |
| Audit | Log `extension.api` access with provider, route id, user, status |
| Rate limits | Per-user and per-provider; upload and expensive routes declare limits |

---

## 8. Compatibility and evolution

- **Manifest version**: integer; core supports versions `1..N`. Unsupported major → extension skipped with warning; provider skills/config still load.
- **Host API**: `requires.host` documents the intended compatibility range. Runtime range validation is deferred.
- **Additive changes** (new optional fields, new extension points) do not bump `version`.
- **Breaking changes** bump `version`; core supports at least the current and previous version for one release cycle.
- **Deprecation**: log once per startup with the replacement field; remove after two releases.
- **Forward compatibility**: unknown fields and message types are ignored; unknown `layout` values fall back; unknown bridge messages are dropped.

---

## 9. Validation and tooling

- Publish `docs/schemas/provider-extension.schema.json` (JSON Schema 2020-12) for the `extensions` block and `runtime-api.json`.
- Core ships a validator:

```bash
python -m app.atlasclaw.tools.validate_provider <provider-dir>
# exit 0 = valid; 1 = invalid; prints JSON diagnostics
```

- Provider conformance tests template:

```
test/test_provider_extension.py
  - manifest validates
  - nav entries resolve to existing files
  - every runtime route handler exists and is async
  - assets stay inside assets_root
  - no core imports beyond host
```

- Core exposes `GET /api/ui/extensions` diagnostics in admin mode:
  - `skipped[]` with `{provider_type, reason}` for invalid manifests.

---

## 10. Conformance checklist

A provider may declare itself v1-conformant when:

- [ ] `PROVIDER.md` + valid `provider.schema.json`
- [ ] `extensions.version` supported by host
- [ ] Every `ui.nav[].entry` exists and every asset is inside `assets_root`
- [ ] Every `runtime-api.json` route references an existing async handler
- [ ] Routes declare `capability` present in `runtime_capabilities`
- [ ] Handler reads only `request` and `context.host`
- [ ] Agent-backed workflows use `context.host.agent`. Use `start_run()` for user-scoped routing and `run_skill()` for an explicitly selected skill.
- [ ] Provider job status uses `queued/running/succeeded/failed/cancelled`
- [ ] Artifacts are copied through `host.agent` and stay inside `host.data_dir`
- [ ] Expected errors expose `status_code`, `code`, and `detail`
- [ ] Binary responses set explicit `Content-Type`
- [ ] UI sends `atlasclaw:ready` and handles `atlasclaw:init`
- [ ] No secrets in UI assets
- [ ] Tests cover permissions, path traversal, and error envelopes

---

## 11. Reference implementation

`audit-report` is the reference provider for v1. Mapping:

| Standard | audit-report usage |
|---|---|
| `extensions.ui.nav` | `文件审核` entry, `access=user`, `layout=full` |
| UI bridge | locale/theme, toast, navigate, title |
| `runtime-api.json` | documents CRUD, upload, audit, file/report download |
| Host Services | `data_dir` for documents/reports, `shared_data_dir` for the standards library |
| Agent invocation | `agent.start_run()` routes each rule batch using the user's enabled skills. The provider tracks job progress and renders HTML. |
| Binary response | original PDF and HTML report |
| Permissions | per-user ownership; admin sees own documents only in v1 |
| Jobs | provider-local status (`pending/running/completed/failed`) until v2 job API |

See `<providers-repo>/docs/plans/2026-11-24-audit-report-provider-design.md` for the full reference design.

---

## 12. Evolution roadmap

| Version | Additions |
|---|---|
| v1 | nav/page, static assets, iframe bridge, provider API, host services, **agent invocation (run_skill/start_run, events, artifacts)**, binary responses |
| v1.1 | settings panels, provider config UI reuse, SSE progress passthrough |
| v2 | jobs API, event bus, widgets, slash commands, batch operations |
| v2.x | multi-tenant admin views, retention policies, provider-to-provider calls |

All future points are additive. Existing v1 providers keep loading without changes.

---

## 13. Open questions

1. Should provider pages be allowed to open external network requests (`connect-src`)? Default: no; provider backend performs external calls.
2. Should admin users see other users' provider documents? Default: no in v1.
3. Where do provider-owned SQLite files live in HA deployments? Default: `workspace/.provider-data/<provider_type>/`; HA requires shared storage or v2 job/store API.
4. Do we expose a provider settings page in v1, or only `config_schema` forms? Default: forms only; settings panel in v1.1.
