# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for internal_request_trace_id workflow context isolation.

Verifies that:
- A. Same skill, two request flows do not cross-contaminate
- B. No trace_id degrades gracefully (backward compatible)
- C. Trace-filtered results still obey max_entries / max_chars limits
- D. Mixed metadata (some with trace, some without) are handled correctly
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.atlasclaw.agent.runner_tool.runner_execution_prepare import (
    _infer_active_request_trace_id,
    _extract_trace_id_from_metadata,
    build_target_md_skill_workflow_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _tool_msg(tool_name: str, internal: Any) -> dict:
    """Create a minimal tool message dict with _internal metadata."""
    return {
        "role": "tool",
        "tool_name": tool_name,
        "content": {
            "success": True,
            "output": "ok",
            "_internal": json.dumps(internal) if isinstance(internal, (dict, list)) else internal,
        },
    }


def _user_msg(text: str = "1") -> dict:
    return {"role": "user", "content": text}


def _assistant_msg(text: str = "已为您查询") -> dict:
    return {"role": "assistant", "content": text}


# ---------------------------------------------------------------------------
# Test: _infer_active_request_trace_id
# ---------------------------------------------------------------------------
class TestInferActiveRequestTraceId:
    def test_returns_most_recent_trace_id(self):
        history = [
            _tool_msg("smartcmp_list_services", {"internal_request_trace_id": "trace-aaa", "catalogs": []}),
            _user_msg("1"),
            _tool_msg("smartcmp_list_business_groups", {"internal_request_trace_id": "trace-aaa", "business_groups": []}),
            _user_msg("1"),
            _tool_msg("smartcmp_list_resource_pools", {"internal_request_trace_id": "trace-bbb", "resource_pools": []}),
        ]
        assert _infer_active_request_trace_id(history) == "trace-bbb"

    def test_returns_none_when_no_trace_id(self):
        history = [
            _tool_msg("smartcmp_list_services", {"catalogs": [{"id": "abc"}]}),
        ]
        assert _infer_active_request_trace_id(history) is None

    def test_handles_list_metadata(self):
        history = [
            _tool_msg("tool_a", [{"internal_request_trace_id": "trace-xyz", "data": "x"}]),
        ]
        assert _infer_active_request_trace_id(history) == "trace-xyz"

    def test_handles_empty_history(self):
        assert _infer_active_request_trace_id([]) is None
        assert _infer_active_request_trace_id(None) is None

    def test_skips_non_tool_messages(self):
        history = [
            _user_msg("hello"),
            _assistant_msg("hi"),
        ]
        assert _infer_active_request_trace_id(history) is None


# ---------------------------------------------------------------------------
# Test: _extract_trace_id_from_metadata
# ---------------------------------------------------------------------------
class TestExtractTraceIdFromMetadata:
    def test_from_dict(self):
        assert _extract_trace_id_from_metadata({"internal_request_trace_id": "trace-123"}) == "trace-123"

    def test_from_list(self):
        meta = [{"internal_request_trace_id": "trace-456", "id": "x"}]
        assert _extract_trace_id_from_metadata(meta) == "trace-456"

    def test_returns_none_for_missing(self):
        assert _extract_trace_id_from_metadata({"catalogs": []}) is None
        assert _extract_trace_id_from_metadata([{"id": "x"}]) is None
        assert _extract_trace_id_from_metadata("string") is None
        assert _extract_trace_id_from_metadata(None) is None


# ---------------------------------------------------------------------------
# Test A: Same skill, two request flows do not cross-contaminate
# ---------------------------------------------------------------------------
class TestTraceIsolation:
    def test_only_collects_current_trace(self):
        """Metadata from trace-aaa should not appear when trace-bbb is active."""
        history = [
            # First request flow (trace-aaa)
            _tool_msg("smartcmp_list_services", {"internal_request_trace_id": "trace-aaa", "catalogs": [{"id": "cat1"}]}),
            _user_msg("1"),
            _tool_msg("smartcmp_list_business_groups", {"internal_request_trace_id": "trace-aaa", "business_groups": [{"id": "bg1"}]}),
            _user_msg("done with first"),
            _assistant_msg("ok"),
            # Second request flow (trace-bbb)
            _tool_msg("smartcmp_list_services", {"internal_request_trace_id": "trace-bbb", "catalogs": [{"id": "cat2"}]}),
            _user_msg("1"),
            _tool_msg("smartcmp_list_business_groups", {"internal_request_trace_id": "trace-bbb", "business_groups": [{"id": "bg2"}]}),
        ]
        result = build_target_md_skill_workflow_context(recent_history=history)
        assert result is not None
        assert result["internal_request_trace_id"] == "trace-bbb"
        # Should only contain trace-bbb entries
        entries = result["recent_tool_metadata"]
        for entry in entries:
            meta = entry["metadata"]
            assert meta.get("internal_request_trace_id") == "trace-bbb"
        # Verify trace-aaa data is excluded
        all_ids = [e["metadata"].get("catalogs", [{}])[0].get("id", "") for e in entries if "catalogs" in e.get("metadata", {})]
        assert "cat1" not in all_ids

    def test_explicit_trace_id_overrides_inferred(self):
        """When active_trace_id is explicitly passed, use it instead of inferring."""
        history = [
            _tool_msg("tool_a", {"internal_request_trace_id": "trace-aaa", "data": "a"}),
            _tool_msg("tool_b", {"internal_request_trace_id": "trace-bbb", "data": "b"}),
        ]
        result = build_target_md_skill_workflow_context(
            recent_history=history,
            active_trace_id="trace-aaa",
        )
        assert result is not None
        assert result["internal_request_trace_id"] == "trace-aaa"
        assert len(result["recent_tool_metadata"]) == 1
        assert result["recent_tool_metadata"][0]["metadata"]["data"] == "a"

    def test_current_trace_narrowing_does_not_mix_other_trace_candidates(self):
        history = [
            _tool_msg(
                "smartcmp_list_services",
                {
                    "internal_request_trace_id": "trace-aaa",
                    "catalogs": [
                        {"id": "cat-aaa-linux", "name": "Linux VM"},
                        {"id": "cat-aaa-windows", "name": "Windows VM"},
                    ],
                },
            ),
            {
                "role": "assistant",
                "content": "old flow",
                "tool_calls": [
                    {
                        "name": "smartcmp_list_available_bgs",
                        "args": {"catalog_id": "cat-aaa-linux"},
                    }
                ],
            },
            _tool_msg(
                "smartcmp_list_services",
                {
                    "internal_request_trace_id": "trace-bbb",
                    "catalogs": [
                        {"id": "cat-bbb-linux", "name": "Linux VM"},
                        {"id": "cat-bbb-windows", "name": "Windows VM"},
                    ],
                },
            ),
            {
                "role": "assistant",
                "content": "new flow",
                "tool_calls": [
                    {
                        "name": "smartcmp_list_available_bgs",
                        "args": {"catalog_id": "cat-bbb-linux"},
                    }
                ],
            },
        ]

        result = build_target_md_skill_workflow_context(recent_history=history)

        assert result is not None
        assert result["internal_request_trace_id"] == "trace-bbb"
        assert result["recent_tool_metadata"] == [
            {
                "tool_name": "smartcmp_list_services",
                "metadata": {
                    "internal_request_trace_id": "trace-bbb",
                    "catalogs": [{"id": "cat-bbb-linux", "name": "Linux VM"}],
                },
            }
        ]

    def test_explicit_trace_narrowing_ignores_later_trace_selection_signals(self):
        history = [
            _tool_msg(
                "smartcmp_list_services",
                {
                    "internal_request_trace_id": "trace-aaa",
                    "catalogs": [
                        {"id": "catalog-linux", "name": "Linux VM"},
                        {"id": "catalog-windows", "name": "Windows VM"},
                    ],
                },
            ),
            {"role": "assistant", "content": "old flow"},
            _tool_msg(
                "smartcmp_list_services",
                {
                    "internal_request_trace_id": "trace-bbb",
                    "catalogs": [
                        {"id": "catalog-linux", "name": "Linux VM"},
                        {"id": "catalog-windows", "name": "Windows VM"},
                    ],
                },
            ),
            {
                "role": "assistant",
                "content": "Linux VM。",
                "tool_calls": [
                    {
                        "name": "smartcmp_list_available_bgs",
                        "args": {"catalog_id": "catalog-linux"},
                    }
                ],
            },
        ]

        result = build_target_md_skill_workflow_context(
            recent_history=history,
            active_trace_id="trace-aaa",
        )

        assert result is not None
        assert result["internal_request_trace_id"] == "trace-aaa"
        assert result["recent_tool_metadata"] == [
            {
                "tool_name": "smartcmp_list_services",
                "metadata": {
                    "internal_request_trace_id": "trace-aaa",
                    "catalogs": [
                        {"id": "catalog-linux", "name": "Linux VM"},
                        {"id": "catalog-windows", "name": "Windows VM"},
                    ],
                },
            }
        ]


# ---------------------------------------------------------------------------
# Test B: No trace_id degrades gracefully (backward compatible)
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    def test_collects_all_when_no_trace_id(self):
        """Legacy providers without trace_id — all _internal entries collected."""
        history = [
            _tool_msg("legacy_tool_1", {"key1": "val1"}),
            _user_msg("next"),
            _tool_msg("legacy_tool_2", {"key2": "val2"}),
        ]
        result = build_target_md_skill_workflow_context(recent_history=history)
        assert result is not None
        assert "internal_request_trace_id" not in result
        assert len(result["recent_tool_metadata"]) == 2

    def test_empty_history_returns_none(self):
        assert build_target_md_skill_workflow_context(recent_history=[]) is None
        assert build_target_md_skill_workflow_context(recent_history=None) is None


# ---------------------------------------------------------------------------
# Test C: Trace-filtered results still obey max_entries / max_chars limits
# ---------------------------------------------------------------------------
class TestLimits:
    def test_max_entries_respected_with_trace(self):
        history = [
            _tool_msg(f"tool_{i}", {"internal_request_trace_id": "trace-x", "idx": i})
            for i in range(10)
        ]
        result = build_target_md_skill_workflow_context(
            recent_history=history,
            max_entries=3,
        )
        assert result is not None
        assert len(result["recent_tool_metadata"]) == 3

    def test_max_chars_respected_with_trace(self):
        # Create entries with large metadata
        history = [
            _tool_msg(f"tool_{i}", {"internal_request_trace_id": "trace-y", "big_data": "x" * 5000})
            for i in range(5)
        ]
        result = build_target_md_skill_workflow_context(
            recent_history=history,
            max_chars=6000,
        )
        assert result is not None
        # Should be limited by character count
        assert len(result["recent_tool_metadata"]) < 5

    def test_single_oversized_metadata_entry_is_omitted(self):
        history = [
            _tool_msg(
                "smartcmp_list_pending",
                [{"requestId": f"REQ{i}", "payload": "x" * 1000} for i in range(50)],
            )
        ]

        assert (
            build_target_md_skill_workflow_context(
                recent_history=history,
                max_chars=6000,
            )
            is None
        )


# ---------------------------------------------------------------------------
# Test D: Mixed metadata (some with trace, some without)
# ---------------------------------------------------------------------------
class TestMixedMetadata:
    def test_entries_without_trace_excluded_when_same_trace_entries_exist(self):
        """When same-trace metadata exists, untraced entries should not mix in by default."""
        history = [
            _tool_msg("tool_no_trace", {"some_data": "abc"}),
            _tool_msg("tool_with_trace", {"internal_request_trace_id": "trace-mix", "data": "xyz"}),
        ]
        result = build_target_md_skill_workflow_context(recent_history=history)
        assert result is not None
        assert result["internal_request_trace_id"] == "trace-mix"
        names = [e["tool_name"] for e in result["recent_tool_metadata"]]
        assert names == ["tool_with_trace"]

    def test_entries_with_different_trace_excluded(self):
        """Only entries with matching trace_id are included when same-trace data exists."""
        history = [
            _tool_msg("tool_old", {"internal_request_trace_id": "trace-old", "data": "old"}),
            _tool_msg("tool_no_trace", {"data": "neutral"}),
            _tool_msg("tool_new", {"internal_request_trace_id": "trace-new", "data": "new"}),
        ]
        result = build_target_md_skill_workflow_context(recent_history=history)
        assert result is not None
        assert result["internal_request_trace_id"] == "trace-new"
        # trace-old and untraced entries should be excluded while same-trace data exists
        names = [e["tool_name"] for e in result["recent_tool_metadata"]]
        assert "tool_old" not in names
        assert "tool_no_trace" not in names
        assert "tool_new" in names

    def test_entries_without_matching_trace_fall_back_to_untraced_metadata(self):
        """If no same-trace metadata survives, legacy untraced entries remain available."""
        history = [
            _tool_msg("tool_old", {"internal_request_trace_id": "trace-old", "data": "old"}),
            _tool_msg("tool_no_trace", {"data": "neutral"}),
        ]
        result = build_target_md_skill_workflow_context(
            recent_history=history,
            active_trace_id="trace-missing",
        )
        assert result is not None
        names = [e["tool_name"] for e in result["recent_tool_metadata"]]
        assert names == ["tool_no_trace"]


# ---------------------------------------------------------------------------
# Test E: No trace — degrade, don't mix recent metadata
# ---------------------------------------------------------------------------
class TestDegradeWithoutTrace:
    def test_no_metadata_returns_none(self):
        history = [
            _user_msg("hello"),
            _assistant_msg("hi there"),
        ]
        assert build_target_md_skill_workflow_context(recent_history=history) is None
