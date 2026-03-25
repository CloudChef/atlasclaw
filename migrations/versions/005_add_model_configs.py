"""Add model_configs table

Revision ID: 005
Revises: 004
Create Date: 2026-03-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key", sa.String(500), nullable=True),
        sa.Column("api_type", sa.String(20), nullable=False, server_default="openai"),
        sa.Column("context_window", sa.Integer(), nullable=False, server_default="128000"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="4096"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_model_configs_name", "model_configs", ["name"])
    op.create_index("ix_model_configs_provider", "model_configs", ["provider"])


def downgrade() -> None:
    op.drop_index("ix_model_configs_provider", table_name="model_configs")
    op.drop_index("ix_model_configs_name", table_name="model_configs")
    op.drop_table("model_configs")
