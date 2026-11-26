# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Persist private, provider-neutral model-call usage."""
from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("model_config_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("token_id", sa.String(100), nullable=False),
        sa.Column("key_label", sa.String(32), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        *(sa.Column(name, sa.Integer(), nullable=True) for name in (
            "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
            "cache_write_tokens", "reasoning_tokens",
        )),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for name in ("user_id", "run_id", "model_config_id", "created_at"):
        op.create_index(f"ix_model_usage_{name}", "model_usage", [name])


def downgrade() -> None:
    op.drop_table("model_usage")
