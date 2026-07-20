"""Critical security-boundary tests for generic Tool confirmation tickets."""

from __future__ import annotations

import pytest

from app.atlasclaw.core.tool_confirmation import (
    ToolConfirmationError,
    ToolConfirmationStore,
)


def _issue(store: ToolConfirmationStore, arguments: dict | None = None):
    return store.issue(
        owner_user_id="user-1",
        session_key="agent:main:user:user-1",
        agent_id="main",
        tool_name="provider_mutation",
        owner_skill_ref="provider:resource",
        contract_fingerprint="contract-v1",
        arguments=arguments or {"ids": ["resource-1"], "action": "restart"},
        provider_type="provider",
        provider_instance="primary",
    )


def test_ticket_is_reused_frozen_and_single_use() -> None:
    store = ToolConfirmationStore()
    arguments = {"ids": ["resource-1"], "action": "restart"}
    ticket = _issue(store, arguments)
    arguments["ids"].append("resource-2")
    assert _issue(store, {"ids": ["resource-1"], "action": "restart"}).token == ticket.token

    grant = store.claim(
        ticket.token,
        owner_user_id="user-1",
        session_key="agent:main:user:user-1",
        agent_id="main",
    )
    assert grant.consume_for(
        tool_name="provider_mutation",
        owner_skill_ref="provider:resource",
        provider_type="provider",
        provider_instance="primary",
        contract_fingerprint="contract-v1",
    ) == {"ids": ["resource-1"], "action": "restart"}
    with pytest.raises(ToolConfirmationError, match="already used"):
        grant.consume_for(
            tool_name="provider_mutation",
            owner_skill_ref="provider:resource",
            provider_type="provider",
            provider_instance="primary",
            contract_fingerprint="contract-v1",
        )
    with pytest.raises(ToolConfirmationError, match="already claimed"):
        store.claim(
            ticket.token,
            owner_user_id="user-1",
            session_key="agent:main:user:user-1",
            agent_id="main",
        )


@pytest.mark.parametrize(
    ("claim_overrides", "error"),
    [
        ({"owner_user_id": "user-2"}, "another user"),
        ({"session_key": "agent:main:user:user-2"}, "another Chat Session"),
        ({"agent_id": "other"}, "another Agent"),
        ({"embed_scope": {"context_id": "forged"}}, "page context changed"),
    ],
)
def test_ticket_rejects_cross_scope_claims(claim_overrides: dict, error: str) -> None:
    store = ToolConfirmationStore()
    ticket = _issue(store)
    claim = {
        "owner_user_id": "user-1",
        "session_key": "agent:main:user:user-1",
        "agent_id": "main",
    }
    claim.update(claim_overrides)
    with pytest.raises(ToolConfirmationError, match=error):
        store.claim(ticket.token, **claim)


def test_ticket_rejects_changed_tool_contract() -> None:
    store = ToolConfirmationStore()
    grant = store.claim(
        _issue(store).token,
        owner_user_id="user-1",
        session_key="agent:main:user:user-1",
        agent_id="main",
    )
    with pytest.raises(ToolConfirmationError, match="contract changed"):
        grant.consume_for(
            tool_name="provider_mutation",
            owner_skill_ref="provider:resource",
            provider_type="provider",
            provider_instance="primary",
            contract_fingerprint="contract-v2",
        )
