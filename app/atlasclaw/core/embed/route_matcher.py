# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Deterministic matching for normalized absolute Host paths."""

from __future__ import annotations

from urllib.parse import unquote

import re

from .models import (
    MatchedRoute,
    RouteManifest,
    RouteRule,
    normalize_absolute_host_path,
)

_PARAMETER_SEGMENT = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def normalize_host_path(path: str) -> str:
    """Normalize a safe absolute Host path without URL or traversal data."""
    return normalize_absolute_host_path(path)


def _match_rule(rule: RouteRule, path: str) -> dict[str, str] | None:
    template_segments = normalize_host_path(rule.match.path_template).strip("/").split("/")
    path_segments = path.strip("/").split("/")
    if len(template_segments) != len(path_segments):
        return None
    parameters: dict[str, str] = {}
    for template_segment, path_segment in zip(template_segments, path_segments):
        parameter = _PARAMETER_SEGMENT.match(template_segment)
        if parameter:
            decoded = unquote(path_segment)
            if not decoded or "/" in decoded or "\\" in decoded:
                return None
            parameters[parameter.group(1)] = decoded
            continue
        if template_segment != path_segment:
            return None
    return parameters


def match_route(manifest: RouteManifest, path: str) -> MatchedRoute | None:
    """Return the highest-priority route match using stable v1 tie breakers."""
    normalized_path = normalize_host_path(path)
    candidates: list[tuple[int, int, int, RouteRule, dict[str, str]]] = []
    for index, rule in enumerate(manifest.routes):
        parameters = _match_rule(rule, normalized_path)
        if parameters is None:
            continue
        static_segments = sum(
            1
            for segment in rule.match.path_template.strip("/").split("/")
            if _PARAMETER_SEGMENT.match(segment) is None
        )
        # Prefer explicit priority, then the most specific template, then
        # manifest order so matching remains deterministic across processes.
        candidates.append((-rule.priority, -static_segments, index, rule, parameters))
    if not candidates:
        return None
    _, _, _, rule, parameters = min(candidates, key=lambda item: item[:3])
    return MatchedRoute(rule=rule, parameters=parameters)
