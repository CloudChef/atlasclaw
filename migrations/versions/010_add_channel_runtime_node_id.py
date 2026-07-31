# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Add the minimal HA runtime owner to Channels.

Revision ID: 010
Revises: 009
Create Date: 2026-07-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column("runtime_node_id", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_channels_runtime_node_id", "channels", ["runtime_node_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_channels_runtime_node_id", table_name="channels")
    op.drop_column("channels", "runtime_node_id")
