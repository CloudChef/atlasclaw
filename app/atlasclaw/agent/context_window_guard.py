# -*- coding: utf-8 -*-
"""Context-window resolution and guard checks for runtime safety."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CONTEXT_WINDOW_HARD_MIN_TOKENS = 16_000
CONTEXT_WINDOW_WARN_BELOW_TOKENS = 32_000


@dataclass
class ContextWindowInfo:
    """Resolved context-window tokens with provenance."""

    tokens: int
    source: str


@dataclass
class ContextWindowGuardResult(ContextWindowInfo):
    """Guard decision for resolved context-window tokens."""

    should_warn: bool
    should_block: bool


def _normalize_positive_int(value: object) -> Optional[int]:
    if not isinstance(value, (int, float)):
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def resolve_context_window_info(
    *,
    selected_token_window: Optional[int],
    models_config_window: Optional[int],
    runtime_override_window: Optional[int],
    default_window: int,
) -> ContextWindowInfo:
    """Resolve context window using a source-aware order with a runtime cap.

    Order:
    1. models_config_window (preferred stable source)
    2. selected_token_window (model metadata source)
    3. default_window fallback
    4. runtime_override_window can cap the resolved value when smaller
    """

    from_models_config = _normalize_positive_int(models_config_window)
    from_model = _normalize_positive_int(selected_token_window)
    from_runtime = _normalize_positive_int(runtime_override_window)
    from_default = _normalize_positive_int(default_window) or 128_000

    if from_models_config:
        base_tokens = from_models_config
        source = "models_config"
    elif from_model:
        base_tokens = from_model
        source = "model"
    elif from_runtime:
        base_tokens = from_runtime
        source = "runtime_override"
    else:
        base_tokens = from_default
        source = "default"

    if from_runtime and from_runtime < base_tokens:
        return ContextWindowInfo(tokens=from_runtime, source="runtime_override")
    return ContextWindowInfo(tokens=base_tokens, source=source)


def evaluate_context_window_guard(
    *,
    tokens: int,
    source: str = "default",
    warn_below_tokens: int = CONTEXT_WINDOW_WARN_BELOW_TOKENS,
    hard_min_tokens: int = CONTEXT_WINDOW_HARD_MIN_TOKENS,
) -> ContextWindowGuardResult:
    """Evaluate warning/block thresholds for context-window tokens."""

    normalized_tokens = max(0, int(tokens or 0))
    normalized_warn = max(1, int(warn_below_tokens or CONTEXT_WINDOW_WARN_BELOW_TOKENS))
    normalized_hard_min = max(1, int(hard_min_tokens or CONTEXT_WINDOW_HARD_MIN_TOKENS))

    return ContextWindowGuardResult(
        tokens=normalized_tokens,
        source=source,
        should_warn=0 < normalized_tokens < normalized_warn,
        should_block=0 < normalized_tokens < normalized_hard_min,
    )
