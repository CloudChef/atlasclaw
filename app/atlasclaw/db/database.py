# -*- coding: utf-8 -*-
"""Database connection management for AtlasClaw.

Supports SQLite (open source) and MySQL (enterprise) backends.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, QueuePool

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class DatabaseConfig:
    """Database configuration schema."""

    def __init__(
        self,
        db_type: str = "sqlite",
        sqlite_path: Optional[str] = None,
        mysql_host: Optional[str] = None,
        mysql_port: int = 3306,
        mysql_database: Optional[str] = None,
        mysql_user: Optional[str] = None,
        mysql_password: Optional[str] = None,
        mysql_charset: str = "utf8mb4",
        pool_size: int = 5,
        max_overflow: int = 10,
        echo: bool = False,
    ):
        self.db_type = db_type
        self.sqlite_path = sqlite_path or "./data/atlasclaw.db"
        self.mysql_host = mysql_host
        self.mysql_port = mysql_port
        self.mysql_database = mysql_database
        self.mysql_user = mysql_user
        self.mysql_password = mysql_password
        self.mysql_charset = mysql_charset
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.echo = echo

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "DatabaseConfig":
        """Create DatabaseConfig from atlasclaw.json config dict."""
        db_config = config.get("database", {})
        db_type = db_config.get("type", "sqlite")

        return cls(
            db_type=db_type,
            sqlite_path=db_config.get("sqlite", {}).get("path", "./data/atlasclaw.db"),
            mysql_host=db_config.get("mysql", {}).get("host"),
            mysql_port=db_config.get("mysql", {}).get("port", 3306),
            mysql_database=db_config.get("mysql", {}).get("database"),
            mysql_user=db_config.get("mysql", {}).get("user"),
            mysql_password=db_config.get("mysql", {}).get("password"),
            mysql_charset=db_config.get("mysql", {}).get("charset", "utf8mb4"),
            pool_size=db_config.get("pool_size", 5),
            max_overflow=db_config.get("max_overflow", 10),
            echo=db_config.get("echo", False),
        )

    def get_connection_url(self) -> str:
        """Build database connection URL."""
        if self.db_type == "sqlite":
            # Ensure parent directory exists
            Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        elif self.db_type == "mysql":
            return (
                f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
                f"?charset={self.mysql_charset}"
            )
        else:
            raise ValueError(f"Unsupported database type: {self.db_type}")


class DatabaseManager:
    """Manages database connections and sessions.

    This is a singleton-like manager that should be initialized once
    at application startup.
    """

    _instance: Optional["DatabaseManager"] = None
    _engine: Optional[AsyncEngine] = None
    _session_factory: Optional[async_sessionmaker[AsyncSession]] = None
    _config: Optional[DatabaseConfig] = None

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def initialize(self, config: DatabaseConfig) -> None:
        """Initialize database connection pool.

        Args:
            config: Database configuration
        """
        self._config = config
        url = config.get_connection_url()

        logger.info(f"Initializing database connection: {config.db_type}")

        # Create engine with appropriate pool settings
        if config.db_type == "sqlite":
            # SQLite doesn't support connection pooling well
            self._engine = create_async_engine(
                url,
                echo=config.echo,
                poolclass=NullPool,
            )
        else:
            # MySQL with connection pooling
            self._engine = create_async_engine(
                url,
                echo=config.echo,
                pool_size=config.pool_size,
                max_overflow=config.max_overflow,
                pool_pre_ping=True,
            )

        # Create session factory
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        logger.info("Database connection initialized successfully")

    async def create_tables(self) -> None:
        """Create all tables (for development/testing)."""
        if self._engine is None:
            raise RuntimeError("Database not initialized")

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Database tables created")

    async def close(self) -> None:
        """Close database connections."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database connections closed")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a database session.

        Usage:
            async with db_manager.get_session() as session:
                # Use session
                pass
        """
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @property
    def engine(self) -> AsyncEngine:
        """Get the database engine."""
        if self._engine is None:
            raise RuntimeError("Database not initialized")
        return self._engine

    @property
    def is_initialized(self) -> bool:
        """Check if database is initialized."""
        return self._engine is not None


# Global convenience functions
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager.get_instance()
    return _db_manager


async def init_database(config: DatabaseConfig) -> DatabaseManager:
    """Initialize the global database manager.

    This should be called once at application startup.
    """
    global _db_manager
    _db_manager = DatabaseManager.get_instance()
    await _db_manager.initialize(config)
    return _db_manager


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Convenience function to get a database session.

    Usage:
        async with get_db_session() as session:
            # Use session
            pass
    """
    manager = get_db_manager()
    async with manager.get_session() as session:
        yield session


async def get_db_session_dependency() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for getting a database session.

    Usage in FastAPI routes:
        @router.get("/items")
        async def list_items(session: AsyncSession = Depends(get_db_session_dependency)):
            # Use session
            pass
    """
    manager = get_db_manager()
    if not manager.is_initialized:
        raise RuntimeError("Database not initialized. Call initialize() first.")
    
    session = manager._session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
