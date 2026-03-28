# External Providers Root Design

**Context**

Xuanwu currently hardcodes provider skill discovery to `app/xuanwu/providers`, while provider templates are not loaded from any configured location at startup. The repository now keeps providers in a separate sibling directory, `../providers`.

**Decision**

Add a top-level `providers_root` field to `xuanwu.json` and `xuanwu.json.example`. Resolve it relative to the loaded config file path. Use this path for both provider template discovery and markdown skill loading. Remove the in-repo `app/xuanwu/providers` directory so Xuanwu no longer depends on built-in providers.

**Scope**

- Add `providers_root` to the runtime config schema.
- Update startup to load provider templates and provider skills from `providers_root`.
- Update shipped config files to use `../providers`.
- Remove `xuanwu/app/xuanwu/providers`.

**Compatibility**

Runtime keeps the same default value in schema as the config templates, so older configs without `providers_root` still resolve to the external providers repository layout.
