# Provider / Skill Model Routing Implementation Plan

> For agentic workers: use subagent-driven-development. Luna implements the scoped tasks; the root agent owns requirements, integration, code review and verification. Do not spawn reviewer agents or commit another worker's files.

**Goal:** Implement the approved Provider Management UI, global Provider/Skill model policies, Role Management permissions, constrained runtime routing and preserved task context across Skill/model boundaries.

**Architecture:** Persist global policies in the existing database. One Core resolver owns safe model references and policy resolution. Runtime calls use per-run execution state and pooled model-bound agents, never mutate a shared Agent model. Reuse the existing capability planner, task history, provider bridges and normalized tool results.

**Tech stack:** Python, FastAPI, SQLAlchemy/Alembic, Pydantic AI, existing vanilla JavaScript pages, Jest and pytest. No new dependencies.

**Spec:** `design.md` in this directory. UI contract: `provider-management.html`.

## Global constraints

- Administrator-only management defaults, delegated through Role Management; global configuration, no role-specific or personal model bindings.
- Preserve existing runtime Provider/Skill grants and upstream permissions.
- Three targets: provider, provider_skill, standalone_skill. Provider is identified by type + instance; skills use canonical registered IDs.
- Only added models appear in configuration. A searchable, paginated add window supplies candidates. Default selection must be in the added set.
- Model references identify a concrete credential configuration, including stable file Token IDs; never use array positions or copy secrets to policies.
- Provider Skill explicit models must be a subset of Provider allowed models; independent Skills inherit platform defaults, not an incidental Provider context.
- No arbitrary audit limits, PDF rasterization, desensitization changes, dependency additions, server deployment or customer-data mutation.
- Existing worktrees have untracked operator artifacts. Do not delete, reset, stage or commit them.
- Each worker writes a Markdown report under `C:/Projects/atlas/outputs/provider-routing/`; leave all source changes uncommitted for root review.

## Shared backend/frontend contract

Base URL: `/api/provider-management`.

Policy JSON:

```json
{"mode":"explicit","allowed_model_refs":["file-token:primary-token"],"default_model_ref":"file-token:primary-token"}
```

An inherited policy is `{ "mode": "inherit", "allowed_model_refs": [], "default_model_ref": null }`.

Model option DTO: `ref`, `provider`, `model_id`, `capabilities`, `source`, `is_active` (safe metadata only).

Provider DTO: `provider_type`, `instance_name`, `name`, `enabled`, `revision`, `model_policy`, `skills`.
Skill DTO: `skill_id`, `name`, `description`, `requires_vision`, `revision`, `model_policy`, optional `effective_model_policy`.

- GET `/providers` -> `{providers: ProviderDTO[]}`.
- GET `/providers/{provider_type}/{instance_name}` -> ProviderDTO.
- PUT same URL -> body `{revision,enabled,model_policy,skills:[{skill_id,model_policy}]}`, response ProviderDTO.
- PATCH same URL + `/activation` -> `{revision,enabled}`, response ProviderDTO.
- GET `/standalone-skills` -> `{skills: SkillDTO[]}`.
- PUT `/standalone-skills/{encoded_skill_id}/model-policy` -> `{revision,model_policy}`, response SkillDTO.
- GET `/model-options?target_kind=...&provider_type=...&instance_name=...&skill_id=...&search=...&page=1&page_size=20` -> `{models: ModelOptionDTO[],total,page,page_size}`.
- Stale revision is HTTP 409. Explicit incompatible/empty/out-of-scope policies are HTTP 422. No permission is HTTP 403. Unknown targets are HTTP 404.
- New permission namespace: `provider_management.{view,enable_disable,configure_models,manage_permissions}`.

### Task 1: Global policies, model catalog, management API and backend permissions

Owner: Luna foundation worker. Independent of frontend implementation.

Expected files: new `db/orm/capability_model_policy.py`, `core/model_bindings.py`, `api/provider_management_routes.py`, migration 015; targeted changes to `db/models.py`, `db/orm/role.py`, `auth/guards.py`, API composition and matching backend tests. Do not edit frontend files, main.py, runner files, provider runtime bridges or provider repository.

Interfaces produced in `core/model_bindings.py`:

```python
@dataclass(frozen=True)
class BindingTarget:
    kind: str
    provider_type: str = ""
    instance_name: str = ""
    skill_id: str = ""

@dataclass
class BindingSelection:
    token: TokenEntry
    eligible_token_ids: set[str]
    policy_id: str | None
    revision: int
    source: str
    reason: str

class ModelBindingResolver:
    def __init__(self, config, skill_registry, provider_registry, token_policy): ...
    async def resolve(self, target: BindingTarget, *, authz=None,
                      required_capabilities: dict | None = None) -> BindingSelection: ...
    async def ensure_provider_enabled(self, provider_type: str, instance_name: str) -> None: ...
```

The resolver uses fresh committed database state, canonical catalog targets, existing permission guards and safe errors. Runtime worker will provide authz and enforce admission. If no policy exists, retain existing model selection semantics. Explicit file bindings must resolve even when managed DB models exist. Tell root immediately if a shared interface needs changing.

- [ ] Add failing tests for stable references, default/subset validation, inherited and explicit resolution, vision requirements, empty allowed set, expired references, optimistic concurrency, permission defaults and denied API writes.
- [ ] Implement policy ORM/migration, safe catalog, resolver and API, updating existing permission normalization/governance rather than bypassing it.
- [ ] Verify toggling changes only activation; complete PUT requires both permissions only when corresponding fields change. API candidate search never returns keys.
- [ ] Run new pytest files and adjacent role/model tests with the existing Python environment and a unique output basetemp.
- [ ] Report changed files, interfaces, tests, limitations and security review notes.

### Task 2: Provider Management UI and Role Management editor

Owner: Luna UI worker. Consume the HTTP contract above; backend may be in progress.

Expected files: new frontend Provider Management page/module/styles, frontend navigation/routes, permission catalog, Role Management module definitions, English and Chinese locale JSON, focused frontend tests. Do not edit Python or provider repository.

- [ ] Add focused tests for denied menu access and button permission differences, Provider/standalone tabs, only-added model display, paginated search/add/remove/default behavior, inherited/explicit Skill policies, stale revision errors and save API payloads.
- [ ] Implement approved light UI. Provider detail handles activation, allowed set/default and child Skill model configuration. Separate standalone tab has no Provider dependency.
- [ ] Candidate picker searches safe API metadata; default choices use added models. Child Skill picker uses Provider scope. The picker must support pending additions to a Provider draft without silently losing unsaved state; if needed request platform candidates and filter against current local Provider draft.
- [ ] Expose `provider_management` flags in Role Management, preserve existing runtime Provider/Skill permissions and bilingual i18n.
- [ ] Verify responsive list, detail and picker; run new/adjacent Jest tests using the known Windows worktree config approach.
- [ ] Report changed files, test evidence and any API mismatch. No backend assumptions hidden in UI.

### Task 3: Runtime binding, Skill stages and durable context

Owner: Luna runtime worker, after reading foundation contract and current planner/runtime.

Expected files: `main.py`, `api/deps_context.py`, agent runner/runtime/prepare/retry/flow code, `core/provider_agent_host.py`, `core/provider_http_runtime.py`, activation filtering in capability/UI discovery, new focused context/stage helper if necessary, runtime tests; provider repo's audit service/bridge tests. Do not edit foundation-owned files or frontend. Coordinate shared Python files with root before editing.

Consume `BindingTarget`, `BindingSelection` and `ModelBindingResolver` defined by Task 1. Initialize on APIContext/runner, never expose resolver or secrets to client context. Existing TokenPool/agent pool APIs remain the execution mechanism.

- [ ] Add failing tests: Provider A selection, selected Skill B override, standalone C independence, no planner call for pinned Skill, authorization failure, constrained failover, cached-session invalidation, parallel user isolation and credential refresh.
- [ ] Route explicitly selected Provider/Skill before inference. For natural language planning, resolve the planner model first and execution binding after validated `target_md_skill`/capability selection. Do not mutate shared agent objects or duplicate tool execution.
- [ ] Implement a real subsequent Skill dispatch boundary using the existing structured intent/capability machinery; a stored field alone is insufficient. Cross-model Skill stages receive normalized task history/results, keep user constraints and return output/artifacts to the same task; preserve tool-call/result pairing. Avoid a speculative orchestration framework.
- [ ] Persist completed stages idempotently in existing task/session stores and recover completed results for subsequent turns/stages. Do not claim transparent mid-LLM resume or exactly-once external writes without evidence.
- [ ] Provider run_text/run_visual resolves fresh credentials with trusted Provider instance/user/Skill identity and vision requirements; audit-report supplies its canonical Skill ID. Keep providers without Skill identifiers compatible.
- [ ] Gate new Provider tasks at admission and remove disabled Provider capabilities from new discovery; do not disable auth/channel plugins or resurrect inactive JSON instances. Keep admitted runs' activation context while rechecking user grants at new Skill boundaries.
- [ ] Tests must exercise actual runtime/model invocations with fake deterministic models or captured transport, not just resolver DTOs; include A -> S1/B -> S2/C with prior result and constraint reaching C, failure result preservation, and no repeat write tool.
- [ ] Report exact code path for dynamic handoff, recovery scope, new event fields, all test commands and any gaps.

### Task 4: Root integration review, fixes and publication

Owner: root. No additional reviewer subagents.

- [ ] Read each worker diff against the design and shared contracts. Check API payloads, default roles/governance, no key disclosure, multi-tenant authorization, atomic writes and cache isolation.
- [ ] Return confirmed defects to the owning Luna worker; re-review fixes. Do not accept scaffolding/tests-only features as runtime completion.
- [ ] Run targeted backend and frontend suites plus adjacent auth, roles, provider host/bridge, tool history, model pool and audit tests. A failing baseline must be identified separately, never hidden.
- [ ] Use local browser tests for configuration/picker/Role Management and a controlled integration run proving model switching and retained context; do not deploy to 12.153 in this request.
- [ ] Update canonical architecture/module documentation and design deviations, preserving no arbitrary audit limits.
- [ ] Commit reviewed code, push feature branches and create reviewable GitHub PRs. The user requested publication and implementation, not automatic merge/deployment of this new feature.

## Review ledger

The main thread keeps task completion, findings and fixes under `.agent/workdir/sp-openspec/provider-skill-model-routing/`. Earlier model UI and provider copy PRs were already merged; no unfinished product diff was found before this change. Untracked browser/operator directories are excluded.
