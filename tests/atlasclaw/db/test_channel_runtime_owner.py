# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Database contract tests for the minimal HA Channel owner field."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.models import ChannelModel
from app.atlasclaw.db.orm.channel_config import ChannelConfigService
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import ChannelCreate, UserCreate


@pytest_asyncio.fixture
async def db_manager(tmp_path: Path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "owner.db"))
    )
    await manager.create_tables()
    yield manager
    await manager.close()


@pytest.mark.asyncio
async def test_create_persists_only_the_runtime_node_owner(db_manager: DatabaseManager) -> None:
    async with db_manager.get_session() as session:
        user = await UserService.create(
            session,
            UserCreate(username="ha-owner", password="password123", roles={}),
        )
        channel = await ChannelConfigService.create(
            session,
            ChannelCreate(
                user_id=user.id,
                name="Owned Feishu",
                type="feishu",
                config={"connection_mode": "longconnection"},
            ),
            runtime_node_id="node-a",
        )

    assert channel.runtime_node_id == "node-a"
    assert not hasattr(channel, "lease_name")
    assert not hasattr(channel, "event_worker_id")


@pytest.mark.asyncio
async def test_runtime_node_queries_do_not_return_another_nodes_channels(
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.get_session() as session:
        user = await UserService.create(
            session,
            UserCreate(username="ha-query", password="password123", roles={}),
        )
        await ChannelConfigService.create(
            session,
            ChannelCreate(user_id=user.id, name="A", type="feishu"),
            runtime_node_id="node-a",
        )
        await ChannelConfigService.create(
            session,
            ChannelCreate(user_id=user.id, name="B", type="dingtalk"),
            runtime_node_id="node-b",
        )
        await ChannelConfigService.create(
            session,
            ChannelCreate(user_id=user.id, name="Legacy", type="wecom"),
        )

        node_a = await ChannelConfigService.list_active_by_runtime_node(session, "node-a")
        owner_ids = await ChannelConfigService.list_runtime_node_ids_by_user(
            session,
            user.id,
        )

    assert [channel.name for channel in node_a] == ["A"]
    assert owner_ids == {"node-a", "node-b"}


@pytest.mark.asyncio
async def test_claim_unassigned_channels_changes_only_null_owners(
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.get_session() as session:
        first_user = await UserService.create(
            session,
            UserCreate(username="legacy-a", password="password123", roles={}),
        )
        second_user = await UserService.create(
            session,
            UserCreate(username="legacy-b", password="password123", roles={}),
        )
        unassigned = await ChannelConfigService.create(
            session,
            ChannelCreate(user_id=first_user.id, name="Legacy", type="feishu"),
        )
        assigned = await ChannelConfigService.create(
            session,
            ChannelCreate(user_id=second_user.id, name="Existing", type="dingtalk"),
            runtime_node_id="node-b",
        )

        claimed = await ChannelConfigService.claim_unassigned_runtime_nodes(
            session,
            "node-a",
        )
        await session.refresh(unassigned)
        await session.refresh(assigned)

    assert claimed == 1
    assert unassigned.runtime_node_id == "node-a"
    assert assigned.runtime_node_id == "node-b"


def test_minimal_ha_models_do_not_define_coordination_or_event_tables() -> None:
    from app.atlasclaw.db import models

    assert hasattr(ChannelModel, "runtime_node_id")
    assert not hasattr(models, "ChannelOutboxEventModel")
    assert not hasattr(models, "ChannelWebhookInboxModel")
