# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Persist global Provider and Skill model policies."""

from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capability_model_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("provider_type", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("instance_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("skill_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("model_mode", sa.String(length=16), nullable=False, server_default="inherit"),
        sa.Column("allowed_model_refs", sa.JSON(), nullable=False),
        sa.Column("default_model_ref", sa.String(length=512), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_kind",
            "provider_type",
            "instance_name",
            "skill_id",
            name="uq_capability_model_policy_target",
        ),
    )
    op.create_index(
        "ix_capability_model_policies_target_kind",
        "capability_model_policies",
        ["target_kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capability_model_policies_target_kind",
        table_name="capability_model_policies",
    )
    op.drop_table("capability_model_policies")
