"""Remove persisted user is_admin flag

Revision ID: 007
Revises: 006
Create Date: 2026-04-07

"""
from __future__ import annotations

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_admin_role(raw_roles: Any) -> bool:
    if isinstance(raw_roles, dict):
        return bool(raw_roles.get("admin"))
    if isinstance(raw_roles, list):
        return any(str(role).lower() == "admin" for role in raw_roles)
    return False


def _with_admin_role(raw_roles: Any) -> Any:
    if isinstance(raw_roles, dict):
        next_roles = dict(raw_roles)
        next_roles["admin"] = True
        return next_roles
    if isinstance(raw_roles, list):
        next_roles = list(raw_roles)
        next_roles.append("admin")
        return next_roles
    return {"admin": True}


def upgrade() -> None:
    bind = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.String(36)),
        sa.column("roles", sa.JSON()),
        sa.column("is_admin", sa.Boolean()),
    )

    rows = bind.execute(
        sa.select(users.c.id, users.c.roles, users.c.is_admin)
    ).mappings().all()

    for row in rows:
        if not bool(row["is_admin"]) or _has_admin_role(row["roles"]):
            continue

        bind.execute(
            sa.update(users)
            .where(users.c.id == row["id"])
            .values(roles=_with_admin_role(row["roles"]))
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_admin")


def downgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    users = sa.table(
        "users",
        sa.column("id", sa.String(36)),
        sa.column("roles", sa.JSON()),
        sa.column("is_admin", sa.Boolean()),
    )

    rows = bind.execute(
        sa.select(users.c.id, users.c.roles)
    ).mappings().all()

    for row in rows:
        bind.execute(
            sa.update(users)
            .where(users.c.id == row["id"])
            .values(is_admin=_has_admin_role(row["roles"]))
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("is_admin", server_default=None)
