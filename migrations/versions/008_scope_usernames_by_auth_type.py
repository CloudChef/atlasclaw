# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Scope usernames by auth type

Revision ID: 008
Revises: 007
Create Date: 2026-04-29

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _users_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "users",
        metadata,
        sa.Column("username", sa.String(100)),
        sa.Column("auth_type", sa.String(100)),
    )


def _raise_if_duplicate_identity_rows() -> None:
    bind = op.get_bind()
    users = _users_table()
    rows = bind.execute(
        sa.select(
            users.c.auth_type,
            users.c.username,
            sa.func.count().label("row_count"),
        )
        .group_by(users.c.auth_type, users.c.username)
        .having(sa.func.count() > 1)
    ).mappings().all()
    if not rows:
        return

    examples = ", ".join(
        f"{row['auth_type']}:{row['username']} ({row['row_count']})"
        for row in rows[:5]
    )
    raise RuntimeError(
        "Cannot add unique constraint on users(auth_type, username); "
        f"duplicate identities exist: {examples}"
    )


def _raise_if_duplicate_username_rows() -> None:
    bind = op.get_bind()
    users = _users_table()
    rows = bind.execute(
        sa.select(
            users.c.username,
            sa.func.count().label("row_count"),
        )
        .group_by(users.c.username)
        .having(sa.func.count() > 1)
    ).mappings().all()
    if not rows:
        return

    examples = ", ".join(
        f"{row['username']} ({row['row_count']})"
        for row in rows[:5]
    )
    raise RuntimeError(
        "Cannot restore unique constraint on users(username); "
        f"duplicate usernames exist: {examples}"
    )


def _username_unique_constraint_names() -> list[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names: list[str] = []
    for constraint in inspector.get_unique_constraints("users"):
        columns = list(constraint.get("column_names") or [])
        if columns == ["username"]:
            names.append(constraint.get("name") or "uq_users_username")
    return names


def _unique_index_names_for_columns(columns: list[str]) -> list[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names: list[str] = []
    for index in inspector.get_indexes("users"):
        if not index.get("unique"):
            continue
        if list(index.get("column_names") or []) == columns:
            name = str(index.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def _index_exists(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == name for index in inspector.get_indexes("users"))


def _ensure_index(name: str, columns: list[str]) -> None:
    if not _index_exists(name):
        op.create_index(name, "users", columns)


def upgrade() -> None:
    _raise_if_duplicate_identity_rows()

    username_unique_constraints = _username_unique_constraint_names()
    username_constraint_names = set(username_unique_constraints)
    username_unique_indexes = [
        name
        for name in _unique_index_names_for_columns(["username"])
        if name not in username_constraint_names
    ]

    with op.batch_alter_table(
        "users",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        for constraint_name in username_unique_constraints:
            batch_op.drop_constraint(constraint_name, type_="unique")
        for index_name in username_unique_indexes:
            batch_op.drop_index(index_name)
        batch_op.create_unique_constraint(
            "uq_users_auth_type_username",
            ["auth_type", "username"],
        )

    _ensure_index("ix_users_username", ["username"])
    _ensure_index("ix_users_auth_type", ["auth_type"])


def downgrade() -> None:
    _raise_if_duplicate_username_rows()

    scoped_unique_indexes = _unique_index_names_for_columns(["auth_type", "username"])

    with op.batch_alter_table(
        "users",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint("uq_users_auth_type_username", type_="unique")
        for index_name in scoped_unique_indexes:
            if index_name != "uq_users_auth_type_username":
                batch_op.drop_index(index_name)
        batch_op.create_unique_constraint("uq_users_username", ["username"])

    _ensure_index("ix_users_username", ["username"])
    _ensure_index("ix_users_auth_type", ["auth_type"])
