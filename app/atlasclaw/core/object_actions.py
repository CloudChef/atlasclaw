# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Provider-agnostic object action extraction for chat sidecar metadata."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

OBJECT_ACTIONS_KEY = "object_actions"
SUPPORTED_ACTION_KINDS = {"open_url", "agent_prompt"}
OBJECT_ACTION_CONTEXT_KEYS: tuple[str, ...] = (
    "index",
    "object_type",
    "object_id",
    "object_name",
)
ACTION_STRING_FIELDS: tuple[str, ...] = (
    "action_id",
    "kind",
    "href",
    "effect",
    "tone",
)
ACTION_LOCALIZED_TEXT_FIELDS: tuple[str, ...] = (
    "display_label",
    "agent_prompt",
    "agent_prompt_template",
    "confirmation_message",
)


def is_safe_action_href(value: Any) -> bool:
    """Return whether a value is a browser-visible HTTP(S) URL with an explicit host."""
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    if not normalized:
        return False
    try:
        parsed = urlparse(normalized)
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(hostname)


def coerce_object_action_payload(payload: Any) -> Any:
    """Parse JSON-looking payload strings before scanning for object actions."""
    if not isinstance(payload, str):
        return payload
    normalized = payload.strip()
    if not normalized or normalized[:1] not in {"{", "["}:
        return payload
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return payload


def collect_object_action_references(
    payload: Any,
    *,
    seen_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Collect provider-declared object actions from nested dict/list payloads.

    Only exact ``object_actions`` keys are recognized. URL-like fields such as
    ``url``, ``href``, ``link``, or ``object_href`` are intentionally ignored so
    providers must opt in to executable object actions explicitly.
    """
    references: list[dict[str, Any]] = []
    seen = seen_keys if seen_keys is not None else set()
    _collect_object_action_references(payload, references=references, seen_keys=seen)
    return references


def collect_object_action_references_from_payloads(
    payloads: list[Any],
    *,
    seen_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Collect deduplicated object action references from multiple tool payloads."""
    references: list[dict[str, Any]] = []
    seen = seen_keys if seen_keys is not None else set()
    for payload in payloads:
        _collect_object_action_references(payload, references=references, seen_keys=seen)
    return references


def collect_latest_object_action_reference_update(
    payloads: list[Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Return the latest provider-declared object actions and publish decision.

    Live streaming and restored chat history use the same replacement semantics:
    the latest payload with object actions replaces earlier actions, while a later
    payload without actions clears earlier controls. A provider that never emits
    valid ``object_actions`` does not trigger an empty protocol update.
    """
    latest_references: list[dict[str, Any]] = []
    saw_object_actions = False
    for payload in payloads:
        references = collect_object_action_references_from_payloads([payload])
        if references:
            saw_object_actions = True
        latest_references = references
    return latest_references, bool(latest_references or saw_object_actions)


def collect_latest_object_action_references_from_payloads(
    payloads: list[Any],
) -> list[dict[str, Any]]:
    """Return only the latest provider-declared object action references."""
    latest_references, _should_publish = collect_latest_object_action_reference_update(payloads)
    return latest_references


def _collect_object_action_references(
    payload: Any,
    *,
    references: list[dict[str, Any]],
    seen_keys: set[str],
) -> None:
    normalized = coerce_object_action_payload(payload)
    if isinstance(normalized, dict):
        _record_object_action_reference(normalized, references=references, seen_keys=seen_keys)
        for key, value in normalized.items():
            if key == OBJECT_ACTIONS_KEY and _normalize_actions(value):
                continue
            if isinstance(coerce_object_action_payload(value), (dict, list)):
                _collect_object_action_references(
                    value,
                    references=references,
                    seen_keys=seen_keys,
                )
        return

    if isinstance(normalized, list):
        for item in normalized:
            _collect_object_action_references(
                item,
                references=references,
                seen_keys=seen_keys,
            )


def _record_object_action_reference(
    payload: dict[Any, Any],
    *,
    references: list[dict[str, Any]],
    seen_keys: set[str],
) -> None:
    actions = _normalize_actions(payload.get(OBJECT_ACTIONS_KEY))
    if not actions:
        return

    reference: dict[str, Any] = {OBJECT_ACTIONS_KEY: actions}
    for key in OBJECT_ACTION_CONTEXT_KEYS:
        if key not in payload:
            continue
        context_value = _context_value(key, payload[key])
        if context_value is not None:
            reference[key] = context_value

    reference_key = _object_action_reference_key(reference)
    if reference_key in seen_keys:
        return
    seen_keys.add(reference_key)
    references.append(reference)


def _normalize_actions(raw_actions: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_actions, list):
        return []
    actions: list[dict[str, Any]] = []
    seen_action_keys: set[str] = set()
    for raw_action in raw_actions:
        action = _normalize_action(raw_action)
        if not action:
            continue
        key = _action_key(action)
        if key in seen_action_keys:
            continue
        seen_action_keys.add(key)
        actions.append(action)
    return actions


def _normalize_action(raw_action: Any) -> dict[str, Any] | None:
    if not isinstance(raw_action, dict):
        return None
    action_id = _string_value(raw_action.get("action_id"))
    kind = _string_value(raw_action.get("kind"))
    if not action_id or kind not in SUPPORTED_ACTION_KINDS:
        return None

    action: dict[str, Any] = {"action_id": action_id, "kind": kind}
    for field in ACTION_STRING_FIELDS:
        value = _string_value(raw_action.get(field))
        if value:
            action[field] = value
    for field in ACTION_LOCALIZED_TEXT_FIELDS:
        value = _localized_text_value(raw_action.get(field))
        if value:
            action[field] = value

    if kind == "open_url":
        href = _string_value(raw_action.get("href"))
        if not is_safe_action_href(href):
            return None
        action["href"] = href
    elif kind == "agent_prompt":
        prompt = _localized_text_value(raw_action.get("agent_prompt")) or _localized_text_value(
            raw_action.get("agent_prompt_template")
        )
        if not prompt:
            return None
    if isinstance(raw_action.get("requires_confirmation"), bool):
        action["requires_confirmation"] = raw_action["requires_confirmation"]
    inputs = _normalize_inputs(raw_action.get("inputs"))
    if inputs:
        action["inputs"] = inputs
    return action


def _normalize_inputs(raw_inputs: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_inputs, list):
        return []
    inputs: list[dict[str, Any]] = []
    for raw_input in raw_inputs:
        if not isinstance(raw_input, dict):
            continue
        name = _string_value(raw_input.get("name"))
        if not name:
            continue
        item: dict[str, Any] = {"name": name}
        for field in ("type",):
            value = _string_value(raw_input.get(field))
            if value:
                item[field] = value
        for field in ("display_label", "placeholder"):
            value = _localized_text_value(raw_input.get(field))
            if value:
                item[field] = value
        if isinstance(raw_input.get("required"), bool):
            item["required"] = raw_input["required"]
        inputs.append(item)
    return inputs


def _context_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    if key == "index":
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized and normalized.lstrip("-").isdigit():
                return int(normalized)
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized if normalized else None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _string_value(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    normalized = str(value).strip()
    return normalized


def _localized_text_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    default = _string_value(value.get("default"))
    if not default:
        return {}

    localized: dict[str, Any] = {"default": default}
    raw_translations = value.get("translations")
    if isinstance(raw_translations, dict):
        translations: dict[str, str] = {}
        for locale, text in raw_translations.items():
            locale_key = _string_value(locale)
            localized_text = _string_value(text)
            if locale_key and localized_text:
                translations[locale_key] = localized_text
        if translations:
            localized["translations"] = translations
    return localized


def _json_safe_mapping(value: dict[Any, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _object_action_reference_key(reference: dict[str, Any]) -> str:
    object_id = reference.get("object_id")
    identity = (
        reference.get("object_type"),
        object_id,
        "" if object_id else reference.get("object_name"),
        reference.get("index"),
    )
    actions = reference.get(OBJECT_ACTIONS_KEY) or []
    action_keys = [_action_key(action) for action in actions if isinstance(action, dict)]
    return json.dumps([identity, action_keys], ensure_ascii=False, sort_keys=True, default=str)


def _action_key(action: dict[str, Any]) -> str:
    stable = {
        "action_id": action.get("action_id"),
        "kind": action.get("kind"),
        "href": action.get("href"),
        "agent_prompt": action.get("agent_prompt"),
        "agent_prompt_template": action.get("agent_prompt_template"),
    }
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
