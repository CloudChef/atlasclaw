# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Add opaque user access tokens.

Revision ID: 011
Revises: 010
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the token table without persisting plaintext credentials."""
    op.create_table(
        "user_access_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("token_hint", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_access_tokens_user_id", "user_access_tokens", ["user_id"])
    op.create_index(
        "ix_user_access_tokens_token_digest",
        "user_access_tokens",
        ["token_digest"],
        unique=True,
    )
    op.create_index("ix_user_access_tokens_revoked_at", "user_access_tokens", ["revoked_at"])


def downgrade() -> None:
    """Remove opaque token storage."""
    op.drop_index("ix_user_access_tokens_revoked_at", table_name="user_access_tokens")
    op.drop_index("ix_user_access_tokens_token_digest", table_name="user_access_tokens")
    op.drop_index("ix_user_access_tokens_user_id", table_name="user_access_tokens")
    op.drop_table("user_access_tokens")
