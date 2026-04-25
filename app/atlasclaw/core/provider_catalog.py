# -*- coding: utf-8 -*-
# Copyright 2021  Qianyun, Inc. All rights reserved.

"""Provider-agnostic helpers for loading configured provider templates."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.bootstrap.startup_helpers import (
    build_provider_instances_from_db,
    merge_provider_instances,
)
from app.atlasclaw.core.config import get_config
from app.atlasclaw.db.database import get_db_manager


def _get_config_provider_instances() -> dict[str, dict[str, dict[str, Any]]]:
    """Return provider templates from atlasclaw.json."""
    return {
        provider_type: {
            instance_name: dict(instance_config)
            for instance_name, instance_config in instances.items()
            if isinstance(instance_config, dict)
        }
        for provider_type, instances in (get_config().service_providers or {}).items()
        if isinstance(instances, dict)
    }


async def get_provider_catalog_instances(
    session: Optional[AsyncSession] = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the provider template catalog from configured runtime sources."""
    config_provider_instances = _get_config_provider_instances()

    if session is not None:
        db_provider_instances = await build_provider_instances_from_db(session)
        if not db_provider_instances:
            return config_provider_instances
        return merge_provider_instances(db_provider_instances, config_provider_instances)

    manager = get_db_manager()
    if not manager.is_initialized:
        return config_provider_instances

    async with manager.get_session() as db_session:
        db_provider_instances = await build_provider_instances_from_db(db_session)
    if not db_provider_instances:
        return config_provider_instances
    return merge_provider_instances(db_provider_instances, config_provider_instances)
