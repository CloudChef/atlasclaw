# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config as AlembicConfig
import pytest


def _write_config(tmp_path: Path, db_path: Path) -> Path:
    project_root = Path(__file__).resolve().parents[3]
    config_payload = json.loads((project_root / "atlasclaw.json").read_text(encoding="utf-8"))
    def _fixed(value: object, fallback: str) -> str:
        text = str(value or "")
        return fallback if text.startswith("${") else text or fallback

    config_payload["workspace"]["path"] = str(tmp_path / ".atlasclaw")
    config_payload["database"]["type"] = "sqlite"
    config_payload["database"]["sqlite"]["path"] = str(db_path)
    config_payload["auth"]["enabled"] = False
    config_payload["auth"]["provider"] = "none"
    config_payload["model"]["temperature"] = 0.2
    for token in config_payload["model"].get("tokens", []):
        token["provider"] = _fixed(token.get("provider"), "openai")
        token["model"] = _fixed(token.get("model"), "gpt-test")
        token["base_url"] = _fixed(token.get("base_url"), "https://example.invalid/v1")
        token["api_key"] = _fixed(token.get("api_key"), "test-key")

    config_path = tmp_path / "atlasclaw.migration-test.json"
    config_path.write_text(
        json.dumps(config_payload, indent=2),
        encoding="utf-8",
    )
    return config_path


def _alembic_config(db_path: Path) -> AlembicConfig:
    project_root = Path(__file__).resolve().parents[3]
    cfg = AlembicConfig(str(project_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


def _force_alembic_url(monkeypatch) -> None:
    import app.atlasclaw.core.config as config_module

    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: (_ for _ in ()).throw(RuntimeError("use alembic test URL")),
    )


def _insert_user(conn: sqlite3.Connection, *, username: str, auth_type: str) -> None:
    now = datetime.utcnow().isoformat(sep=" ")
    conn.execute(
        """
        INSERT INTO users (
            id,
            username,
            email,
            password,
            auth_type,
            roles,
            is_active,
            display_name,
            avatar_url,
            last_login_at,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            username,
            None,
            None,
            auth_type,
            "{}",
            1,
            username,
            None,
            None,
            now,
            now,
        ),
    )
    conn.commit()


def test_sqlite_migration_scopes_usernames_by_auth_type(tmp_path, monkeypatch):
    db_path = tmp_path / "atlasclaw.db"
    config_path = _write_config(tmp_path, db_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    _force_alembic_url(monkeypatch)
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "007")
    command.upgrade(cfg, "head")

    with sqlite3.connect(db_path) as conn:
        _insert_user(conn, username="admin", auth_type="local")
        _insert_user(conn, username="admin", auth_type="cookie")

        with pytest.raises(sqlite3.IntegrityError):
            _insert_user(conn, username="admin", auth_type="cookie")


def test_sqlite_migration_fails_when_duplicate_scoped_usernames_exist(tmp_path, monkeypatch):
    db_path = tmp_path / "duplicate-scoped.db"
    config_path = _write_config(tmp_path, db_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    _force_alembic_url(monkeypatch)
    cfg = _alembic_config(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version (version_num) VALUES ('007')")
        conn.execute(
            """
            CREATE TABLE users (
                id VARCHAR(36) NOT NULL,
                username VARCHAR(100),
                email VARCHAR(255),
                password VARCHAR(255),
                auth_type VARCHAR(100),
                roles JSON,
                is_active BOOLEAN,
                display_name VARCHAR(255),
                avatar_url VARCHAR(500),
                last_login_at DATETIME,
                created_at DATETIME,
                updated_at DATETIME,
                PRIMARY KEY (id)
            )
            """
        )
        _insert_user(conn, username="admin", auth_type="cookie")
        _insert_user(conn, username="admin", auth_type="cookie")

    with pytest.raises(RuntimeError, match="duplicate identities exist"):
        command.upgrade(cfg, "head")


def test_sqlite_downgrade_fails_when_duplicate_usernames_exist(tmp_path, monkeypatch):
    db_path = tmp_path / "duplicate-usernames.db"
    config_path = _write_config(tmp_path, db_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    _force_alembic_url(monkeypatch)
    cfg = _alembic_config(db_path)

    command.upgrade(cfg, "head")

    with sqlite3.connect(db_path) as conn:
        _insert_user(conn, username="admin", auth_type="local")
        _insert_user(conn, username="admin", auth_type="cookie")

    with pytest.raises(RuntimeError, match="duplicate usernames exist"):
        command.downgrade(cfg, "007")
