# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Server-owned image capability snapshots for a single run."""

from __future__ import annotations

from sqlalchemy import select

from app.atlasclaw.db.database import get_db_manager
from app.atlasclaw.db.models import ModelConfigModel
from app.atlasclaw.db.orm.model_config import ModelConfigService


async def image_capabilities(runner) -> dict[str, bool]:
    """
    Return token ID to declared image capability for the current run.

    Refresh database-backed declarations; changed endpoints must match the runtime
    configuration before use. Disabled models and missing flags are ineligible.
    """
    policy = getattr(runner, "token_policy", None)
    if policy is None:
        return {}
    tokens = policy.token_pool.tokens
    result = {key: token.capabilities.get("vision") is True for key, token in tokens.items()}
    source_ids = {t.source_model_config_id for t in tokens.values() if t.source_model_config_id}
    if source_ids:
        manager = get_db_manager()
        async with manager.get_session() as session:
            rows = (await session.scalars(
                select(ModelConfigModel).where(ModelConfigModel.id.in_(source_ids))
            )).all()
            available = {row.id: row for row in rows}
            for key, token in tokens.items():
                if not token.source_model_config_id:
                    continue
                row = available.get(token.source_model_config_id)
                result[key] = bool(row and row.is_active
                    and (row.provider, row.model_id, (row.base_url or "").rstrip("/"))
                    == (token.provider, token.model, token.base_url.rstrip("/")) and (
                    ModelConfigService.get_capabilities(row) or {}
                ).get("vision") is True)
    return result


def session_model(runner, session_key):
    """
    Inspect the session, primary, or fallback token without creating a session pin.

    Return None when no token policy or available token is found.
    """
    policy = getattr(runner, "token_policy", None)
    if policy is None:
        return None
    selected = policy.get_session_token(session_key)
    if selected:
        return selected
    primary = policy.token_pool.tokens.get(policy.primary_token_id)
    health = policy.token_pool.get_token_health(policy.primary_token_id)
    if primary and health and health.is_healthy:
        return primary
    return policy.token_pool.select_token(strategy=policy.strategy)
