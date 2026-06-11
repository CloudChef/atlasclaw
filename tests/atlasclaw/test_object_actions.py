# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest

from app.atlasclaw.core.object_actions import (
    collect_latest_object_action_reference_update,
    collect_latest_object_action_references_from_payloads,
    collect_object_action_references_from_payloads,
)


def localized(default: str, zh_cn: str | None = None) -> dict[str, object]:
    return {
        "default": default,
        "translations": {
            "en-US": default,
            "zh-CN": zh_cn or default,
        },
    }


def test_collects_nested_object_actions_and_context_fields() -> None:
    payload = {
        "object_type": "approval_request",
        "object_id": "REQ-001",
        "object_name": "Provision VM",
        "index": 1,
        "requestId": "REQ-001",
        "object_actions": [
            {
                "action_id": "open_detail",
                "kind": "open_url",
                "display_label": localized("Open", "打开"),
                "href": "https://console.example.com/requests/REQ-001",
            },
            {
                "action_id": "approve",
                "kind": "agent_prompt",
                "display_label": localized("Approve", "同意"),
                "agent_prompt": localized("Approve REQ-001", "批准 REQ-001"),
                "confirmation_message": localized("Confirm approving REQ-001?", "确认同意 REQ-001？"),
                "effect": "mutate",
                "tone": "success",
                "requires_confirmation": True,
            },
        ],
        "children": [
            {
                "object_type": "cloud_resource",
                "object_id": "vm-001",
                "object_name": "Linux VM",
                "resourceId": "vm-001",
                "object_actions": [
                    {
                        "action_id": "open_detail",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/vm-001",
                    }
                ],
            }
        ],
    }

    refs = collect_object_action_references_from_payloads([payload])

    assert refs == [
        {
            "object_actions": [
                {
                    "action_id": "open_detail",
                    "kind": "open_url",
                    "display_label": localized("Open", "打开"),
                    "href": "https://console.example.com/requests/REQ-001",
                },
                {
                    "action_id": "approve",
                    "kind": "agent_prompt",
                    "display_label": localized("Approve", "同意"),
                    "agent_prompt": localized("Approve REQ-001", "批准 REQ-001"),
                    "confirmation_message": localized("Confirm approving REQ-001?", "确认同意 REQ-001？"),
                    "effect": "mutate",
                    "tone": "success",
                    "requires_confirmation": True,
                },
            ],
            "index": 1,
            "object_type": "approval_request",
            "object_id": "REQ-001",
            "object_name": "Provision VM",
        },
        {
            "object_actions": [
                {
                    "action_id": "open_detail",
                    "kind": "open_url",
                    "href": "https://console.example.com/resources/vm-001",
                }
            ],
            "object_type": "cloud_resource",
            "object_id": "vm-001",
            "object_name": "Linux VM",
        },
    ]


def test_parses_json_strings_and_internal_metadata() -> None:
    refs = collect_object_action_references_from_payloads(
        [
            (
                '{"items":[{"object_id":"vm-1","object_actions":[{"action_id":"open",'
                '"kind":"open_url","href":"https://console.example.com/vms/vm-1"}]}]}'
            ),
            {
                "success": True,
                "_internal": (
                    '[{"object_id":"REQ-002","object_actions":[{"action_id":"view_detail",'
                    '"kind":"agent_prompt","agent_prompt":{"default":"Show approval details for REQ-002",'
                    '"translations":{"zh-CN":"查看 REQ-002 的审批详情",'
                    '"en-US":"Show approval details for REQ-002"}}}]}]'
                ),
            },
        ]
    )

    assert refs == [
        {
            "object_actions": [
                {
                    "action_id": "open",
                    "kind": "open_url",
                    "href": "https://console.example.com/vms/vm-1",
                }
            ],
            "object_id": "vm-1",
        },
        {
            "object_actions": [
                {
                    "action_id": "view_detail",
                    "kind": "agent_prompt",
                    "agent_prompt": localized(
                        "Show approval details for REQ-002",
                        "查看 REQ-002 的审批详情",
                    ),
                }
            ],
            "object_id": "REQ-002",
        },
    ]


def test_normalizes_standard_context_and_ignores_alias_fields() -> None:
    refs = collect_object_action_references_from_payloads(
        [
            {
                "object_actions": [
                    {
                        "action_id": "open",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/123",
                    }
                ],
                "index": "7",
                "object_id": 123,
                "object_name": 456.0,
                "requestId": 456.0,
                "resourceId": "resource-123",
            },
            {
                "object_actions": [
                    {
                        "action_id": "open",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/invalid-index",
                    }
                ],
                "index": 1.5,
                "id": "invalid-index",
            },
        ]
    )

    assert refs == [
        {
            "object_actions": [
                {
                    "action_id": "open",
                    "kind": "open_url",
                    "href": "https://console.example.com/resources/123",
                }
            ],
            "index": 7,
            "object_id": "123",
            "object_name": "456.0",
        },
        {
            "object_actions": [
                {
                    "action_id": "open",
                    "kind": "open_url",
                    "href": "https://console.example.com/resources/invalid-index",
                }
            ],
        },
    ]


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "data:text/html,<h1>x</h1>",
        "file:///tmp/report.html",
        "workspace://users/alice/work_dir/report.html",
        "/relative/path",
        "relative/path",
        "",
        "   ",
        "https:///missing-host",
        "http://",
        "http://[::1",
    ],
)
def test_rejects_unsafe_open_url_action_hrefs(href: str) -> None:
    refs = collect_object_action_references_from_payloads(
        [
            {
                "object_id": "unsafe",
                "object_actions": [{"action_id": "open", "kind": "open_url", "href": href}],
            }
        ]
    )

    assert refs == []


def test_rejects_reserved_execute_tool_actions_until_execution_contract_exists() -> None:
    refs = collect_object_action_references_from_payloads(
        [
            {
                "object_id": "tool-backed-object",
                "object_actions": [
                    {
                        "action_id": "restart",
                        "kind": "execute_tool",
                        "executor": {"tool_name": "provider_restart"},
                    }
                ],
            }
        ]
    )

    assert refs == []


def test_ignores_url_href_link_object_href_and_non_exact_keys() -> None:
    payload = {
        "url": "https://console.example.com/url",
        "href": "https://console.example.com/href",
        "link": "https://console.example.com/link",
        "object_href": "https://console.example.com/old",
        "objectActions": [
            {
                "action_id": "open",
                "kind": "open_url",
                "href": "https://console.example.com/camel-case",
            }
        ],
        "nested": {"url": "https://console.example.com/nested-url"},
    }

    refs = collect_object_action_references_from_payloads([payload])

    assert refs == []


def test_deduplicates_by_object_identity_and_actions_preserving_first_context() -> None:
    refs = collect_object_action_references_from_payloads(
        [
            {
                "object_id": "vm-1",
                "object_name": "First title",
                "object_actions": [
                    {
                        "action_id": "open",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/vm-1",
                    }
                ],
            },
            {
                "object_id": "vm-1",
                "object_name": "Second title",
                "object_actions": [
                    {
                        "action_id": "open",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/vm-1",
                    }
                ],
            },
        ]
    )

    assert refs == [
        {
            "object_actions": [
                {
                    "action_id": "open",
                    "kind": "open_url",
                    "href": "https://console.example.com/resources/vm-1",
                }
            ],
            "object_id": "vm-1",
            "object_name": "First title",
        }
    ]


def test_rejects_agent_prompt_actions_without_localized_prompt() -> None:
    refs = collect_object_action_references_from_payloads(
        [
            {
                "object_type": "vm",
                "object_name": "alpha",
                "object_actions": [
                    {
                        "action_id": "inspect",
                        "kind": "agent_prompt",
                        "prompt": "查看详情",
                    }
                ],
            }
        ]
    )

    assert refs == []


def test_deduplicates_name_only_objects_by_object_name() -> None:
    refs = collect_object_action_references_from_payloads(
        [
            {
                "object_type": "vm",
                "object_name": "alpha",
                "object_actions": [
                    {
                        "action_id": "inspect",
                        "kind": "agent_prompt",
                        "agent_prompt": localized("Inspect alpha", "查看 alpha 详情"),
                    }
                ],
            },
            {
                "object_type": "vm",
                "object_name": "beta",
                "object_actions": [
                    {
                        "action_id": "inspect",
                        "kind": "agent_prompt",
                        "agent_prompt": localized("Inspect beta", "查看 beta 详情"),
                    }
                ],
            },
        ]
    )

    assert [ref["object_name"] for ref in refs] == ["alpha", "beta"]


def test_latest_object_action_update_uses_last_payload_and_clears_prior_actions() -> None:
    refs, should_publish = collect_latest_object_action_reference_update(
        [
            {
                "object_id": "vm-1",
                "object_actions": [
                    {
                        "action_id": "open",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/vm-1",
                    }
                ],
            },
            {"message": "latest tool result has no actions"},
        ]
    )

    assert refs == []
    assert should_publish is True


def test_latest_object_action_update_ignores_providers_without_protocol_actions() -> None:
    refs, should_publish = collect_latest_object_action_reference_update(
        [
            {"url": "https://console.example.com/plain-url"},
            {"href": "https://console.example.com/plain-href"},
        ]
    )

    assert refs == []
    assert should_publish is False


def test_latest_object_action_references_from_payloads_returns_latest_reference_only() -> None:
    refs = collect_latest_object_action_references_from_payloads(
        [
            {
                "object_id": "vm-1",
                "object_actions": [
                    {
                        "action_id": "open",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/vm-1",
                    }
                ],
            },
            {
                "object_id": "vm-2",
                "object_actions": [
                    {
                        "action_id": "open",
                        "kind": "open_url",
                        "href": "https://console.example.com/resources/vm-2",
                    }
                ],
            },
        ]
    )

    assert refs == [
        {
            "object_actions": [
                {
                    "action_id": "open",
                    "kind": "open_url",
                    "href": "https://console.example.com/resources/vm-2",
                }
            ],
            "object_id": "vm-2",
        }
    ]
