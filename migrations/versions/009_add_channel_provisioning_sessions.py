# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Add channel provisioning sessions

Revision ID: 009
Revises: 008
Create Date: 2026-05-02

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_provisioning_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("channel_type", sa.String(50), nullable=False),
        sa.Column("state_token", sa.String(255), nullable=False),
        sa.Column("user_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("qr_url", sa.Text(), nullable=True),
        sa.Column("qr_image_url", sa.Text(), nullable=True),
        sa.Column("instructions_i18n_key", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("connection_id", sa.String(36), nullable=True),
        sa.Column("connection_name", sa.String(100), nullable=True),
        sa.Column("platform_state", sa.JSON(), nullable=True),
        sa.Column("refresh_after_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_channel_provisioning_sessions_user_id",
        "channel_provisioning_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_channel_provisioning_sessions_channel_type",
        "channel_provisioning_sessions",
        ["channel_type"],
    )
    op.create_index(
        "ix_channel_provisioning_sessions_state_token",
        "channel_provisioning_sessions",
        ["state_token"],
    )
    op.create_index(
        "ix_channel_provisioning_sessions_user_code",
        "channel_provisioning_sessions",
        ["user_code"],
    )
    op.create_index(
        "ix_channel_provisioning_sessions_status",
        "channel_provisioning_sessions",
        ["status"],
    )
    op.create_index(
        "ix_channel_provisioning_sessions_expires_at",
        "channel_provisioning_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_provisioning_sessions_expires_at", table_name="channel_provisioning_sessions")
    op.drop_index("ix_channel_provisioning_sessions_status", table_name="channel_provisioning_sessions")
    op.drop_index("ix_channel_provisioning_sessions_user_code", table_name="channel_provisioning_sessions")
    op.drop_index("ix_channel_provisioning_sessions_state_token", table_name="channel_provisioning_sessions")
    op.drop_index("ix_channel_provisioning_sessions_channel_type", table_name="channel_provisioning_sessions")
    op.drop_index("ix_channel_provisioning_sessions_user_id", table_name="channel_provisioning_sessions")
    op.drop_table("channel_provisioning_sessions")
