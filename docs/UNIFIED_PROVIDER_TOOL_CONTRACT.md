# AtlasClaw Unified Provider/Tool Contract (v1)

## 1. Goal

This document defines a single metadata contract for:

- built-in tools
- provider-injected tools
- skill-declared executable tools

The runtime must treat them as the same class of callable tool objects, then let LLM decide whether and how to call them in one unified loop.

## 2. Core Model

### 2.1 Concept boundaries

- Provider: integration namespace and connection context (for example `jira`, `smartcmp`, `servicenow`).
- Skill: instruction package (`SKILL.md`) that can optionally declare one or more executable tools.
- Tool: callable runtime unit exposed to LLM.

Skill is not tool; skill can produce 0..N tools.

### 2.2 Canonical runtime tool record

Every callable tool must be normalized to:

```json
{
  "name": "smartcmp_list_pending",
  "description": "List pending approvals from SmartCMP",
  "source": "provider",
  "provider_type": "smartcmp",
  "group_ids": ["group:smartcmp", "group:cmp"],
  "capability_class": "provider:smartcmp",
  "priority": 100,
  "category": "skill",
  "location": "provider"
}
```

### 2.3 Required fields and rules

| Field | Type | Required | Rule |
|---|---|---:|---|
| `name` | string | yes | globally unique tool id |
| `description` | string | yes | concise action description for LLM |
| `source` | enum | yes | `builtin` \| `provider` \| `md_skill` |
| `provider_type` | string | no | required for provider tools |
| `group_ids` | string[] | yes | every item must be normalized to `group:*` |
| `capability_class` | string | yes | semantic routing key; provider tools must use `provider:<type>` |
| `priority` | int | no | default 100 |
| `category` | string | no | informational |
| `location` | string | no | `built-in`, `provider`, `workspace`, `user`, etc. |

## 3. Provider Metadata Contract (PROVIDER.md and provider.schema.json)

Provider LLM routing metadata lives in `PROVIDER.md` frontmatter. Runtime/API/UI
configuration metadata lives in the fixed sibling file `provider.schema.json`.
Runtime must not parse config schema, auth fields, labels, placeholders, icons,
or redaction rules from `PROVIDER.md` body tables.

### 3.1 Required provider keys

| Key | Type | Required | Example |
|---|---|---:|---|
| `provider_type` | string | yes | `smartcmp` |
| `display_name` | string | yes | `SmartCMP` |
| `version` | string | yes | `1.0.0` |

### 3.2 Recommended discovery keys

| Key | Type | Purpose |
|---|---|---|
| `keywords` | string[] | lexical hints for tool-gate/provider intent |
| `capabilities` | string[] | high-level provider capabilities |
| `use_when` | string[] | when this provider should be used |
| `avoid_when` | string[] | when it should not be used |

### 3.3 Provider frontmatter example

```yaml
---
provider_type: smartcmp
display_name: SmartCMP
version: "1.0.0"
keywords:
  - cmp
  - approval
  - service catalog
capabilities:
  - list pending approvals
  - get request details
  - list service catalogs
use_when:
  - user asks for CMP approvals, tickets, catalogs
avoid_when:
  - user asks generic internet questions
---
```

### 3.4 Provider manifest example

```json
{
  "schema_version": 1,
  "provider_type": "smartcmp",
  "catalog": {
    "display_name": "SmartCMP",
    "badge": "CMP",
    "icon": "SC",
    "icon_path": "assets/icon.svg",
    "accent": "#0f766e",
    "description": "Enterprise CMP workflow provider."
  },
  "config_schema": {
    "default_auth_type": "user_token",
    "auth_modes": {
      "user_token": { "required_fields": ["user_token"] },
      "credential": { "required_fields": ["username", "password"] }
    },
    "fields": [
      { "name": "base_url", "type": "url", "required": true, "scope": "instance" },
      { "name": "auth_type", "type": "hidden", "default": "user_token", "scope": "instance" },
      { "name": "user_token", "type": "password", "required": true, "sensitive": true, "scope": "user", "auth_types": ["user_token"] }
    ]
  }
}
```

## 4. Skill Metadata Contract (SKILL.md)

`SKILL.md` frontmatter defines both instruction metadata and optional executable tool registration.

### 4.1 Required skill keys

| Key | Type | Required |
|---|---|---:|
| `name` | string | yes |
| `description` | string | yes |

### 4.2 Provider-skill keys

| Key | Type | Required | Note |
|---|---|---:|---|
| `provider_type` | string | recommended | required for provider-scope tools |
| `instance_required` | bool/string | optional | whether provider instance must be selected |
| `use_when` | string[] | recommended | discovery guidance |
| `avoid_when` | string[] | optional | disambiguation |
| `triggers` | string[] | optional | discovery hints |

### 4.3 Executable tool declaration

Canonical single-tool form (recommended):

```yaml
tool_default_name: "jira_issue_get"
tool_default_entrypoint: "scripts/jira_issue_get.py:handler"
tool_default_description: "Get Jira issue by key"
tool_default_group: "jira"
tool_default_capability_class: "provider:jira"
tool_default_priority: 100
```

Legacy single-tool compatibility:

```yaml
tool_name: "jira_issue_get"
entrypoint: "scripts/jira_issue_get.py:handler"
```

Multi-tool form:

```yaml
tool_get_name: "jira_issue_get"
tool_get_entrypoint: "scripts/jira_issue_get.py:handler"
tool_get_description: "Get Jira issue by key"
tool_get_group: "jira"
tool_get_capability_class: "provider:jira"
tool_get_priority: 100

tool_update_name: "jira_issue_update"
tool_update_entrypoint: "scripts/jira_issue_update.py:handler"
tool_update_description: "Update Jira issue fields"
tool_update_groups:
  - jira
  - issue
tool_update_capability_class: "provider:jira"
tool_update_priority: 120
```

### 4.4 Group metadata keys

Supported key aliases:

- global: `group`, `groups`, `tool_group`, `tool_groups`
- per-tool: `tool_<id>_group`, `tool_<id>_groups`

Runtime normalization rule:

- if value does not start with `group:`, prepend `group:`
- provider tools automatically add `group:<provider_type>`
- built-ins additionally add `group:atlasclaw`

### 4.5 Capability metadata keys

Supported key aliases:

- global: `capability_class`
- per-tool: `tool_<id>_capability_class`

Fallback inference order:

1. explicit per-tool capability
2. explicit global capability
3. `provider:<provider_type>` if provider exists
4. built-in special cases (`web_search`, `web_fetch`, `browser`, `openmeteo_weather`)
5. `skill`

## 5. Policy and Minimal Toolset Selection

Toolset selection is deterministic and metadata-driven, before model loop.

### 5.1 Pipeline order

1. `profile`
2. `global`
3. `by_provider[provider_type]`
4. `by_agent[agent_id]`
5. `channel[channel]`
6. `by_session[session_key]` (optional)

### 5.2 Per-layer semantics

For each layer:

1. expand `group:*`, aliases, and globs
2. apply `deny` first
3. if `allow` empty -> keep current set
4. if `allow` non-empty -> intersect

Set is monotonic shrinking only.

### 5.3 Policy source

Policy is runtime config payload (`deps.extra.toolset_policy`), not generated by LLM.
LLM only chooses from already filtered tools.

## 6. Prompt Contract to LLM

### 6.1 Skills section

Only compact index is injected:

- `name`
- short `description`
- `file_path`

LLM instruction:

- when details are needed, call `read` on skill `file_path` (`SKILL.md`)
- do not assume full skill text is already in context

### 6.2 Tools section

Expose only filtered runtime toolset for the current turn.
All tool types (builtin/provider/skills) appear in one list and one loop.

## 7. How to Add Metadata in Practice

### 7.1 Add provider metadata

1. edit `providers/<provider>/PROVIDER.md` frontmatter
2. set `provider_type`, `display_name`, `version`
3. add `keywords/capabilities/use_when/avoid_when`
4. edit `providers/<provider>/provider.schema.json`
5. set catalog metadata, config fields, auth modes, aliases, defaults, sensitive flags, and optional redaction hints

### 7.2 Add skill metadata

1. edit `skills/<skill>/SKILL.md` frontmatter
2. set `name`, `description`, optional `provider_type`
3. add `tool_<id>_name` + `tool_<id>_entrypoint`
4. add per-tool `group/capability_class/priority`

### 7.3 Validation checklist

- each tool has non-empty `name/description`
- entrypoint file exists
- capability class is explicit for provider tools
- groups are normalized to `group:*`
- no duplicate tool names across all loaded sources

## 8. Group Baseline (AtlasClaw built-ins)

- `group:runtime` -> `exec`, `process`
- `group:fs` -> `read`, `write`, `edit`
- `group:web` -> `web_search`, `web_fetch`, `openmeteo_weather`
- `group:ui` -> `browser`
- `group:memory` -> `memory_search`, `memory_get`
- `group:sessions` -> `sessions_*`, `subagents`, `session_status`
- `group:providers` -> `list_provider_instances`, `select_provider_instance`
- `group:atlasclaw` -> union of built-in groups

Provider groups are additive (for example `group:cmp`, `group:jira`).

## 9. Backward Compatibility Rules

To avoid breaking existing skill packages:

1. keep supporting old aliases (`tool_name`/`entrypoint`, `tool_<id>_*`)
2. if new metadata missing, fallback to existing inference rules
3. reject only hard-invalid cases (missing required name/description/entrypoint)

## 10. Non-Goals

- no keyword hardcoded business routing
- no special runtime branch only for web tools
- no provider-specific fork in model loop

All tools must follow the same contract and same loop semantics.

## 11. Complete Provider Metadata Schema

This section is normative for provider frontmatter keys.

### 11.1 Required keys

| Key | Type | Required | Validation |
|---|---|---:|---|
| `provider_type` | string | yes | lowercase id, stable namespace |
| `display_name` | string | yes | user-facing provider name |
| `version` | string | yes | semantic version string recommended |

### 11.2 Recommended keys

| Key | Type | Required | Runtime usage |
|---|---|---:|---|
| `keywords` | string[] | recommended | provider intent matching hints |
| `capabilities` | string[] | recommended | high-level capability summary |
| `use_when` | string[] | recommended | positive routing signals |
| `avoid_when` | string[] | recommended | negative routing signals |

### 11.3 Optional/derived fields

| Field | Source | Note |
|---|---|---|
| `description` | first body paragraph | derived by runtime from markdown body |

## 12. Complete Skill Metadata Schema (SKILL.md)

This section is normative for skill frontmatter keys.

### 12.1 Required keys

| Key | Type | Required | Validation |
|---|---|---:|---|
| `name` | string | yes | skill id |
| `description` | string | yes | concise purpose |

### 12.2 Provider-skill recommended keys

| Key | Type | Required | Runtime usage |
|---|---|---:|---|
| `provider_type` | string | recommended | provider namespace |
| `instance_required` | bool/string | recommended | enforce instance-selection flow |
| `use_when` | string[] | recommended | discovery guidance |
| `avoid_when` | string[] | recommended | disambiguation guidance |
| `triggers` | string[] | recommended | lexical trigger hints |

### 12.3 Optional general keys

| Key | Type | Note |
|---|---|---|
| `category` | string | default `skill` |
| `examples` | string[] | prompt-side discovery context |
| `related` | string[] | neighboring skill ids |
| `group` / `groups` | string/string[] | global group hints |
| `tool_group` / `tool_groups` | string/string[] | alias of group hints |
| `capability_class` | string | global capability fallback |
| `priority` | int | global priority fallback |

### 12.4 Executable tool declaration keys

For each tool id `<id>`, the canonical key pair is:

- `tool_<id>_name` (required)
- `tool_<id>_entrypoint` (required)

Optional per-tool keys:

- `tool_<id>_description`
- `tool_<id>_group`
- `tool_<id>_groups`
- `tool_<id>_capability_class`
- `tool_<id>_priority`

## 13. Enterprise Provider Examples

The following examples show standard metadata patterns for SmartCMP, ServiceNow, Datadog, and CyberArk.

### 13.1 SmartCMP

Provider `PROVIDER.md` frontmatter:

```yaml
---
provider_type: smartcmp
display_name: SmartCMP
version: "1.0.0"
keywords:
  - cmp
  - approval
  - service catalog
capabilities:
  - list pending approvals
  - get request details
  - list service catalogs
use_when:
  - user asks for cmp approvals, request details, or service catalogs
avoid_when:
  - user asks generic internet questions
---
```

Skill `SKILL.md` frontmatter:

```yaml
---
name: approval
description: Approval workflow skill for SmartCMP
provider_type: smartcmp
instance_required: true
triggers:
  - pending approvals
  - approve request
  - reject request
use_when:
  - user wants pending approvals
  - user wants to approve or reject cmp requests
avoid_when:
  - user asks to create new resource request
tool_list_name: smartcmp_list_pending
tool_list_entrypoint: scripts/list_pending.py
tool_list_group: cmp
tool_list_capability_class: provider:smartcmp
tool_list_priority: 100
tool_get_name: smartcmp_get_request_detail
tool_get_entrypoint: scripts/get_request_detail.py
tool_get_groups:
  - cmp
  - approval
tool_get_capability_class: provider:smartcmp
tool_get_priority: 110
---
```

### 13.2 ServiceNow

Provider `PROVIDER.md` frontmatter:

```yaml
---
provider_type: servicenow
display_name: ServiceNow
version: "1.0.0"
keywords:
  - servicenow
  - incident
  - change request
capabilities:
  - query incidents
  - create and update incidents
  - list service catalogs
use_when:
  - user asks for incident/change/service request operations
avoid_when:
  - user asks pure code repository operations
---
```

Skill `SKILL.md` frontmatter:

```yaml
---
name: servicenow-incident
description: Incident CRUD skill for ServiceNow
provider_type: servicenow
instance_required: true
triggers:
  - incident detail
  - create incident
  - update incident
use_when:
  - user requests incident lifecycle operations
tool_default_name: servicenow_incident_get
tool_default_entrypoint: scripts/incident_get.py:handler
tool_default_group: servicenow
tool_default_capability_class: provider:servicenow
tool_default_priority: 100
tool_update_name: servicenow_incident_update
tool_update_entrypoint: scripts/incident_update.py:handler
tool_update_group: servicenow
tool_update_capability_class: provider:servicenow
tool_update_priority: 120
---
```

### 13.3 Datadog

Provider `PROVIDER.md` frontmatter:

```yaml
---
provider_type: datadog
display_name: Datadog
version: "1.0.0"
keywords:
  - datadog
  - monitor
  - apm
  - alert
capabilities:
  - list monitors
  - get monitor detail
  - mute or unmute monitor
use_when:
  - user asks for observability or monitor operations
avoid_when:
  - user asks ticketing operations
---
```

Skill `SKILL.md` frontmatter:

```yaml
---
name: datadog-monitor
description: Datadog monitor operations skill
provider_type: datadog
instance_required: true
triggers:
  - monitor list
  - alert detail
  - mute monitor
use_when:
  - user asks for monitor status and alert handling
tool_list_name: datadog_monitor_list
tool_list_entrypoint: scripts/monitor_list.py:handler
tool_list_group: datadog
tool_list_capability_class: provider:datadog
tool_list_priority: 100
tool_mute_name: datadog_monitor_mute
tool_mute_entrypoint: scripts/monitor_mute.py:handler
tool_mute_groups:
  - datadog
  - alerts
tool_mute_capability_class: provider:datadog
tool_mute_priority: 120
---
```

### 13.4 CyberArk

Provider `PROVIDER.md` frontmatter:

```yaml
---
provider_type: cyberark
display_name: CyberArk
version: "1.0.0"
keywords:
  - cyberark
  - privileged access
  - secret rotation
capabilities:
  - query vault account
  - rotate secret
  - check rotation status
use_when:
  - user asks for privileged secret management
avoid_when:
  - user asks generic weather/news queries
---
```

Skill `SKILL.md` frontmatter:

```yaml
---
name: cyberark-secret-ops
description: Secret query and rotation skill for CyberArk
provider_type: cyberark
instance_required: true
triggers:
  - rotate password
  - vault secret
  - privileged account
use_when:
  - user needs secret lookup or rotation in cyberark
tool_get_name: cyberark_secret_get
tool_get_entrypoint: scripts/secret_get.py:handler
tool_get_group: cyberark
tool_get_capability_class: provider:cyberark
tool_get_priority: 100
tool_rotate_name: cyberark_secret_rotate
tool_rotate_entrypoint: scripts/secret_rotate.py:handler
tool_rotate_groups:
  - cyberark
  - pam
tool_rotate_capability_class: provider:cyberark
tool_rotate_priority: 130
---
```

## 14. Implementation Notes

1. Keep provider and skill metadata language-agnostic; avoid hardcoded language routing.
2. Do not enforce business intent with keyword hardcoding in runtime branching.
3. All provider/skill tools must be visible as unified tool records before model loop.
4. LLM decides tool call order inside the loop; runtime only enforces contract, policy, and safety boundaries.

## 15. Related Design Spec

For the runtime behavior of how provider/skill metadata influences tool ordering without hardcoded routing, see:

- `docs/superpowers/specs/2026-04-08-provider-skill-metadata-hint-layer-design.md`
