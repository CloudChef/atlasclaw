# -*- coding: utf-8 -*-

from __future__ import annotations

from app.atlasclaw.agent.context_window_guard import (
    CONTEXT_WINDOW_HARD_MIN_TOKENS,
    CONTEXT_WINDOW_WARN_BELOW_TOKENS,
    evaluate_context_window_guard,
    resolve_context_window_info,
)


def test_resolve_context_window_info_prefers_models_config_and_runtime_cap():
    info = resolve_context_window_info(
        selected_token_window=200000,
        models_config_window=128000,
        runtime_override_window=64000,
        default_window=32000,
    )
    assert info.tokens == 64000
    assert info.source == "runtime_override"


def test_resolve_context_window_info_falls_back_to_model_then_default():
    model_info = resolve_context_window_info(
        selected_token_window=96000,
        models_config_window=None,
        runtime_override_window=None,
        default_window=32000,
    )
    assert model_info.tokens == 96000
    assert model_info.source == "model"

    default_info = resolve_context_window_info(
        selected_token_window=None,
        models_config_window=None,
        runtime_override_window=None,
        default_window=32000,
    )
    assert default_info.tokens == 32000
    assert default_info.source == "default"


def test_evaluate_context_window_guard_warn_and_block_thresholds():
    guard = evaluate_context_window_guard(tokens=12000)
    assert guard.should_warn is True
    assert guard.should_block is True

    guard_warn_only = evaluate_context_window_guard(tokens=20000)
    assert guard_warn_only.should_warn is True
    assert guard_warn_only.should_block is False

    guard_ok = evaluate_context_window_guard(tokens=64000)
    assert guard_ok.should_warn is False
    assert guard_ok.should_block is False

    assert CONTEXT_WINDOW_HARD_MIN_TOKENS < CONTEXT_WINDOW_WARN_BELOW_TOKENS
