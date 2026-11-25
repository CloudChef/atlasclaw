# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Add document attachment columns to chat_attachments (SQLite and MySQL)."""
from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add generic attachment metadata; existing rows stay image attachments."""
    with op.batch_alter_table("chat_attachments") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="image"))
        batch.add_column(sa.Column("extension", sa.String(16), nullable=False, server_default=""))
        batch.add_column(sa.Column("metadata_json", sa.Text(), nullable=True))
        batch.alter_column("width", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("height", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Drop generic attachment metadata; document rows are not converted."""
    with op.batch_alter_table("chat_attachments") as batch:
        batch.drop_column("metadata_json")
        batch.drop_column("extension")
        batch.drop_column("kind")
        batch.alter_column("width", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("height", existing_type=sa.Integer(), nullable=False)
