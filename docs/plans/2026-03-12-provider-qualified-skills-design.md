# Provider-Qualified Skills Design

**Context**

Xuanwu currently loads provider skills from `providers_root`, but they are not consistently registered as `provider:skill`. This leaves room for name collisions and forces webhook dispatch to depend on a separate `webhook.skill_sources` loading path.

**Decision**

Make `providers_root` the single source of provider skills. Every provider skill discovered under `providers_root/<provider>/skills/` must be registered with a qualified name in the form `provider:skill`. Remove `webhook.skill_sources` entirely.

**Namespace Rule**

Provider namespaces are derived from provider directory names using a stable normalization rule:

- lowercase the directory name
- replace non-alphanumeric separators with `-`
- strip a trailing `-provider`

Examples:

- `jira` -> `jira`
- `SmartCMP-Provider` -> `smartcmp`

**Impact**

- Provider markdown skills are always namespaced.
- Webhook dispatch resolves allowed skills only from already-loaded provider skills.
- `xuanwu.json` no longer needs `webhook.skill_sources`.
