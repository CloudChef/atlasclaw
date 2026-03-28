# External Providers Root Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move Xuanwu provider discovery to a configurable external `providers_root` and remove the built-in provider directory dependency.

**Architecture:** Add a top-level config field that points to the providers repository, resolve it against the loaded config file directory, and use it consistently for provider template discovery and markdown skill loading during application startup. Keep a matching schema default so existing configs without the new key still start against the external repo layout.

**Tech Stack:** Python, Pydantic, FastAPI, JSON config files

---

### Task 1: Add config surface for external providers root

**Files:**
- Modify: `xuanwu/app/xuanwu/core/config_schema.py`
- Modify: `xuanwu/xuanwu.json`
- Modify: `xuanwu/xuanwu.json.example`

**Step 1: Add the new schema field**

Add a top-level `providers_root: str` field to `XuanwuConfig` with default `../providers`.

**Step 2: Update shipped config**

Add `providers_root` to `xuanwu.json` and point provider webhook skill roots at the external providers repository.

**Step 3: Update example config**

Add `providers_root` to `xuanwu.json.example` and update example skill roots to the external providers repository.

**Step 4: Verify config files parse**

Run: `python3 - <<'PY'\nimport json\njson.load(open('xuanwu/xuanwu.json'))\njson.load(open('xuanwu/xuanwu.json.example'))\nprint('json-ok')\nPY`

Expected: `json-ok`

### Task 2: Move startup provider discovery to the configured root

**Files:**
- Modify: `xuanwu/app/xuanwu/main.py`

**Step 1: Resolve provider root**

Compute `providers_root` from `config.providers_root`, resolving it against the loaded config directory when available.

**Step 2: Load provider templates**

Call `ServiceProviderRegistry.load_from_directory()` with the resolved providers root before loading configured instances.

**Step 3: Load markdown skills from external providers**

Replace the hardcoded `app/xuanwu/providers` scan with a scan of the configured providers root and load each provider's `skills/` directory.

**Step 4: Verify syntax**

Run: `python3 -m compileall xuanwu/app/xuanwu/main.py xuanwu/app/xuanwu/core/config_schema.py`

Expected: successful compilation with no errors

### Task 3: Remove the built-in providers directory

**Files:**
- Delete: `xuanwu/app/xuanwu/providers/`

**Step 1: Remove in-repo provider assets**

Delete the built-in provider directory now that runtime points at the external providers repository.

**Step 2: Verify no runtime references remain**

Run: `rg -n "app/xuanwu/providers" xuanwu/app xuanwu/xuanwu.json xuanwu/xuanwu.json.example`

Expected: no matches
