# Provider/Skill model routing verification

Verified on 2026-11-27 against core main `704aa1c` and providers main `f0d5c84`.

## Implemented behavior

- Provider Management has separate Provider and standalone Skill tabs, global activation, inherited or explicit model policies, and searchable paginated model addition. Child and standalone editors use drawers. Configuration shows added models only.
- Role Management controls the four management rights independently of execution grants. Built-in admin rights remain canonical; delegated user/custom-role rights persist.
- The resolver uses current committed credentials, concrete file/DB references, Skill requirements and actual image inputs. A failed model cannot escape the effective compatible allowed set.
- The public runner executes successive Skills on their bound models while retaining original constraints, follow-up requests, normalized tool pairs and complete evidence. Bound executable and Markdown Skills are both supported. Internal context envelopes are not added as user messages.
- Completed stages link their existing usage rows and emit safe binding/start/completion events. Provider text/visual bridges apply the same policy, refresh grants and credentials, and record actual selected-model usage.
- A saved task is adopted only after a validated continuation decision. Completed answers can be replayed without tools. Interrupted writes with unknown outcomes require reconciliation and are not blindly repeated.

## Verification results

| Check | Result |
|---|---|
| Core backend integration and adjacent regressions | 489 passed, 1 expected failure |
| Audit provider handler regression | 65 passed |
| Frontend Provider/Role/Model Management and locale regressions | 91 passed across 6 suites |
| Real browser with production management API and synthetic data | Passed |
| Frontend production build | Passed; existing query-string dynamic-import glob warnings remain |
| Diff whitespace and credential-pattern checks | Passed |

The expected failure is the existing startup test that requires external LLM environment variables. Model-routing tests use deterministic FunctionModel agents or captured transport rather than external model services.

The public runner integration executes A → B → C → D with real model/tool code paths and deterministic coordinator plans. It checks complete prior evidence, the original and planned inputs, an inherited middle-stage policy, one execution per write tool, one final answer, safe stage events, failed-task continuation, interrupted-stage rejection and unrelated new tasks.

The browser check used 27 synthetic model configurations and verified second-page child selection, search and focus retention, persisted overrides, vision filtering, Chinese labels and a 480px layout. It used no customer reports or cloud calls.

The backend run covered these suites:

```text
test_model_bindings, api/test_provider_management_api,
db/test_user_auth_type_migration, api/test_role_crud_api,
test_model_configs, test_role_skill_permissions, test_model_binding_runtime,
test_skill_stage_context, test_model_binding_context_security,
test_model_binding_activation_admission, test_runner_execution_runtime,
test_agent_no_provider_guard, test_standard_skill_runtime,
test_runner_tool_execution_contract, test_provider_agent_host,
test_main_startup, test_token_pool_components, test_model_usage,
test_provider_tool_groups, test_agent_capabilities, test_embed_api_routes,
test_channel_manager, test_webhook_dispatch, auth/test_sliding_session,
test_ui_extensions_api, test_provider_http_runtime,
test_provider_http_runtime_v1, test_provider_visual_runtime,
test_provider_bridge_binding_auth, test_provider_bridge_usage
```

All are under `tests/atlasclaw/` with a `.py` suffix. The provider run used `providers/audit-report/test/test_handlers.py`. Frontend suites were `provider-management`, `provider-management-page`, `role-management`, `models-page`, `models-locale-refresh` and `i18n`, run from `app/frontend` so existing locale fixtures resolve correctly.

An existing startup/tool-group test-order leak was reproduced on clean main. The tool-group fixture now isolates and restores the global API context; production authentication behavior was not changed for that test issue.

## Integration notes

Migration 015 adds the policy table. Its SQLite upgrade/downgrade test preserves an existing user row; no external database migration was performed.

Deploy the companion core change before the audit provider change, which supplies the new optional canonical Skill ID to the Core bridge. This verification did not deploy to 192.168.12.153 or make live OpenRouter requests. Direct CLI code bypassing Core does not acquire these policies automatically.

The visual bridge now also accepts an omitted/`None` timeout, matching the text bridge. Long-running Provider audits await the model without an added host deadline; explicitly supplied deadlines still return 504 when exceeded. The visual, bridge authorization and bridge usage suites passed together (28 tests) after this contract change.
