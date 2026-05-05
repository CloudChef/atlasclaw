# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
import re
from typing import Any


_DSML_TAG_DELIMITER = r"[｜|]{1,2}"
_DSML_INVOKE_PATTERN = re.compile(
    rf"<{_DSML_TAG_DELIMITER}DSML{_DSML_TAG_DELIMITER}invoke\s+name=\"(?P<name>[^\"]+)\"\s*>"
    rf"(?P<body>.*?)</{_DSML_TAG_DELIMITER}DSML{_DSML_TAG_DELIMITER}invoke>",
    flags=re.IGNORECASE | re.DOTALL,
)
_DSML_PARAMETER_PATTERN = re.compile(
    rf"<{_DSML_TAG_DELIMITER}DSML{_DSML_TAG_DELIMITER}parameter\s+name=\"(?P<name>[^\"]+)\""
    rf"(?:\s+string=\"(?P<string>[^\"]+)\")?\s*>(?P<value>.*?)"
    rf"</{_DSML_TAG_DELIMITER}DSML{_DSML_TAG_DELIMITER}parameter>",
    flags=re.IGNORECASE | re.DOTALL,
)
_PLAINTEXT_TOOL_CALL_PATTERN = re.compile(
    rf"</?tool_call\b|<web_search\b|<web_fetch\b|<browser\b|<function_call\b|"
    rf"<{_DSML_TAG_DELIMITER}DSML{_DSML_TAG_DELIMITER}"
    rf"(?:tool_calls|function_calls|invoke)\b|</think\s*>",
    flags=re.IGNORECASE,
)


def looks_like_plaintext_tool_call_attempt(text: str) -> bool:
    """Return true when text resembles a leaked tool-call payload."""
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return bool(_PLAINTEXT_TOOL_CALL_PATTERN.search(normalized))


def parse_plaintext_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse text-form tool calls such as DeepSeek DSML markup into normalized tool-call dicts."""
    normalized = str(text or "").strip()
    if not normalized:
        return []

    parsed_calls = _parse_dsml_tool_calls(normalized)
    if parsed_calls:
        return parsed_calls
    return []


def _parse_dsml_tool_calls(text: str) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for match in _DSML_INVOKE_PATTERN.finditer(text):
        tool_name = str(match.group("name") or "").strip()
        if not tool_name:
            continue
        args: dict[str, Any] = {}
        body = str(match.group("body") or "")
        for parameter_match in _DSML_PARAMETER_PATTERN.finditer(body):
            param_name = str(parameter_match.group("name") or "").strip()
            if not param_name:
                continue
            param_value = _coerce_dsml_value(
                raw_value=str(parameter_match.group("value") or ""),
                string_flag=str(parameter_match.group("string") or "").strip().lower(),
            )
            args[param_name] = param_value
        normalized_call: dict[str, Any] = {"name": tool_name}
        if args:
            normalized_call["args"] = args
        tool_calls.append(normalized_call)
    return tool_calls


def _coerce_dsml_value(*, raw_value: str, string_flag: str) -> Any:
    value = str(raw_value or "").strip()
    if string_flag == "true":
        return value
    if not value:
        return ""

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None

    if value.startswith(("{", "[", "\"")):
        try:
            return json.loads(value)
        except Exception:
            return value

    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except Exception:
            return value

    if re.fullmatch(r"-?\d+\.\d+", value):
        try:
            return float(value)
        except Exception:
            return value

    return value
