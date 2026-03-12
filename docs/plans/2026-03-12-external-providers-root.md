# External Providers Root Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move AtlasClaw provider discovery to a configurable external `providers_root` and remove the built-in provider directory dependency.

**Architecture:** Add a top-level config field that points to the providers repository, resolve it against the loaded config file directory, and use it consistently for provider template discovery and markdown skill loading during application startup. Keep a matching schema default so existing configs without the new key still start against the external repo layout.

**Tech Stack:** Python, Pydantic, FastAPI, JSON config files

---

### Task 1: Add config surface for external providers root

**Files:**
- Modify: `atlasclaw/app/atlasclaw/core/config_schema.py`
- Modify: `atlasclaw/atlasclaw.json`
- Modify: `atlasclaw/atlasclaw.json.example`

**Step 1: Add the new schema field**

Add a top-level `providers_root: str` field to `AtlasClawConfig` with default `../atlasclaw-providers/providers`.

**Step 2: Update shipped config**

Add `providers_root` to `atlasclaw.json` and point provider webhook skill roots at the external providers repository.

**Step 3: Update example config**

Add `providers_root` to `atlasclaw.json.example` and update example skill roots to the external providers repository.

**Step 4: Verify config files parse**

Run: `python3 - <<'PY'\nimport json\njson.load(open('atlasclaw/atlasclaw.json'))\njson.load(open('atlasclaw/atlasclaw.json.example'))\nprint('json-ok')\nPY`

Expected: `json-ok`

### Task 2: Move startup provider discovery to the configured root

**Files:**
- Modify: `atlasclaw/app/atlasclaw/main.py`

**Step 1: Resolve provider root**

Compute `providers_root` from `config.providers_root`, resolving it against the loaded config directory when available.

**Step 2: Load provider templates**

Call `ServiceProviderRegistry.load_from_directory()` with the resolved providers root before loading configured instances.

**Step 3: Load markdown skills from external providers**

Replace the hardcoded `app/atlasclaw/providers` scan with a scan of the configured providers root and load each provider's `skills/` directory.

**Step 4: Verify syntax**

Run: `python3 -m compileall atlasclaw/app/atlasclaw/main.py atlasclaw/app/atlasclaw/core/config_schema.py`

Expected: successful compilation with no errors

### Task 3: Remove the built-in providers directory

**Files:**
- Delete: `atlasclaw/app/atlasclaw/providers/`

**Step 1: Remove in-repo provider assets**

Delete the built-in provider directory now that runtime points at the external providers repository.

**Step 2: Verify no runtime references remain**

Run: `rg -n "app/atlasclaw/providers" atlasclaw/app atlasclaw/atlasclaw.json atlasclaw/atlasclaw.json.example`

Expected: no matches
