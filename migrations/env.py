# -*- coding: utf-8 -*-
"""Alembic migration environment configuration."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import models to ensure they are registered with Base.metadata
from app.xuanwu.db.models import Base
from app.xuanwu.core.config import get_config

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata


def get_url() -> str:
    """Get database URL from configuration."""
    try:
        xuanwu_config = get_config()
        db_config = xuanwu_config.database

        if hasattr(db_config, "get"):
            # Dict format
            db_type = db_config.get("type", "sqlite")
            if db_type == "sqlite":
                path = db_config.get("sqlite", {}).get("path", "./data/xuanwu.db")
                return f"sqlite+aiosqlite:///{path}"
            elif db_type == "mysql":
                mysql = db_config.get("mysql", {})
                return (
                    f"mysql+aiomysql://{mysql.get('user')}:{mysql.get('password')}"
                    f"@{mysql.get('host')}:{mysql.get('port', 3306)}/{mysql.get('database')}"
                    f"?charset={mysql.get('charset', 'utf8mb4')}"
                )
        else:
            # Pydantic model format
            db_type = getattr(db_config, "type", "sqlite")
            if db_type == "sqlite":
                path = getattr(db_config, "sqlite_path", "./data/xuanwu.db")
                return f"sqlite+aiosqlite:///{path}"
            elif db_type == "mysql":
                return (
                    f"mysql+aiomysql://{db_config.mysql_user}:{db_config.mysql_password}"
                    f"@{db_config.mysql_host}:{db_config.mysql_port}/{db_config.mysql_database}"
                    f"?charset={db_config.mysql_charset}"
                )
    except Exception:
        pass

    # Fallback to alembic.ini setting
    return config.get_main_option("sqlalchemy.url", "sqlite+aiosqlite:///./data/xuanwu.db")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
