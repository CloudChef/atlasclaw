# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Service operations for User configuration."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import bcrypt
from sqlalchemy import select, func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db.models import UserModel, generate_uuid
from app.atlasclaw.db.schemas import UserCreate, UserUpdate

logger = logging.getLogger(__name__)
_LEGACY_SQLITE_USERS_IS_ADMIN_COLUMN_CACHE: Dict[str, bool] = {}


def _normalize_required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def _normalize_optional_text(value: Any) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_auth_type(value: Any) -> str:
    return (_normalize_optional_text(value) or "local").lower()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    Args:
        password: Plain text password

    Returns:
        Hashed password string
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_digest: str) -> bool:
    """Verify a password against a hash.

    Args:
        password: Plain text password
        password_digest: Hashed password

    Returns:
        True if password matches
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_digest.encode("utf-8"))
    except Exception:
        return False


def _has_role_assignment(raw_roles: Any, role_identifier: str) -> bool:
    """Return whether a role identifier is assigned in either dict or legacy list storage."""
    normalized_role_identifier = str(role_identifier or "").strip().lower()
    if not normalized_role_identifier:
        return False

    if isinstance(raw_roles, dict):
        return any(
            bool(enabled) and str(identifier or "").strip().lower() == normalized_role_identifier
            for identifier, enabled in raw_roles.items()
        )

    if isinstance(raw_roles, list):
        return any(
            str(identifier or "").strip().lower() == normalized_role_identifier
            for identifier in raw_roles
        )

    return False


async def _has_legacy_sqlite_users_is_admin_column(session: AsyncSession) -> bool:
    """Return whether the connected SQLite users table still requires the legacy is_admin column."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "sqlite":
        return False

    cache_key = str(bind.engine.url)
    cached = _LEGACY_SQLITE_USERS_IS_ADMIN_COLUMN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result = await session.execute(text("PRAGMA table_info(users)"))
    has_legacy_column = any(str(row[1]) == "is_admin" for row in result.fetchall())
    _LEGACY_SQLITE_USERS_IS_ADMIN_COLUMN_CACHE[cache_key] = has_legacy_column
    return has_legacy_column


async def _create_user_for_legacy_sqlite_schema(
    session: AsyncSession,
    user_data: UserCreate,
    *,
    password_digest: Optional[str],
) -> UserModel:
    """Insert a user into legacy SQLite schemas that still require the is_admin column."""
    user_id = generate_uuid()
    user = UserModel(
        id=user_id,
        username=user_data.username,
        email=user_data.email,
        password=password_digest,
        auth_type=getattr(user_data, "auth_type", "local") or "local",
        display_name=user_data.display_name,
        roles=user_data.roles,
        is_active=user_data.is_active,
    )
    now = datetime.utcnow()

    await session.execute(
        text("""
            INSERT INTO users (
                id,
                username,
                email,
                password,
                auth_type,
                roles,
                is_active,
                is_admin,
                display_name,
                avatar_url,
                last_login_at,
                created_at,
                updated_at
            ) VALUES (
                :id,
                :username,
                :email,
                :password,
                :auth_type,
                :roles,
                :is_active,
                :is_admin,
                :display_name,
                :avatar_url,
                :last_login_at,
                :created_at,
                :updated_at
            )
        """),
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "password": user.password,
            "auth_type": user.auth_type,
            "roles": json.dumps(user.roles) if user.roles is not None else None,
            "is_active": user.is_active,
            "is_admin": _has_role_assignment(user.roles, "admin"),
            "display_name": user.display_name,
            "avatar_url": None,
            "last_login_at": None,
            "created_at": now,
            "updated_at": now,
        },
    )

    created_user = await UserService.get_by_id(session, user.id)
    if created_user is None:
        raise RuntimeError(f"Failed to load newly created user '{user.username}' from legacy schema")
    return created_user



class UserService:
    """Service operations for User configuration."""

    @staticmethod
    async def create(session: AsyncSession, user_data: UserCreate) -> UserModel:
        """Create a new User.

        Args:
            session: Database session
            user_data: User creation data

        Returns:
            Created User model
        """
        normalized_user_data = UserCreate(
            username=_normalize_required_text(user_data.username, "username"),
            email=_normalize_optional_text(user_data.email),
            password=user_data.password,
            display_name=_normalize_optional_text(user_data.display_name),
            roles=user_data.roles,
            auth_type=_normalize_auth_type(getattr(user_data, "auth_type", "local")),
            is_active=user_data.is_active,
        )

        # Hash password if provided
        password = None
        if normalized_user_data.password:
            password = hash_password(normalized_user_data.password)

        if await _has_legacy_sqlite_users_is_admin_column(session):
            user = await _create_user_for_legacy_sqlite_schema(
                session,
                normalized_user_data,
                password_digest=password,
            )
            logger.info(f"Created user: {user.username} (id={user.id})")
            return user

        user = UserModel(
            username=normalized_user_data.username,
            email=normalized_user_data.email,
            password=password,
            auth_type=normalized_user_data.auth_type,
            display_name=normalized_user_data.display_name,
            roles=normalized_user_data.roles,
            is_active=normalized_user_data.is_active,
        )

        session.add(user)
        await session.flush()
        await session.refresh(user)
        logger.info(f"Created user: {user.username} (id={user.id})")
        return user

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: str) -> Optional[UserModel]:
        """Get User by ID.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            User model or None
        """
        user_id = _normalize_optional_text(user_id)
        if not user_id:
            return None

        result = await session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_username(
        session: AsyncSession,
        username: str,
        *,
        auth_type: Optional[str] = None,
    ) -> Optional[UserModel]:
        """Get User by username, optionally scoped by auth type.

        Args:
            session: Database session
            username: Username
            auth_type: Optional auth type scope

        Returns:
            User model or None
        """
        username = _normalize_optional_text(username)
        if not username:
            return None

        query = select(UserModel).where(UserModel.username == username)
        if auth_type is not None:
            query = query.where(UserModel.auth_type == _normalize_auth_type(auth_type))

        result = await session.execute(query)
        return result.scalars().first()

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> Optional[UserModel]:
        """Get User by email.

        Args:
            session: Database session
            email: Email address

        Returns:
            User model or None
        """
        email = _normalize_optional_text(email)
        if not email:
            return None

        result = await session.execute(
            select(UserModel).where(UserModel.email == email)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def authenticate(
        session: AsyncSession, username: str, password: str
    ) -> Optional[UserModel]:
        """Authenticate a user by username and password.

        Args:
            session: Database session
            username: Username
            password: Plain text password

        Returns:
            User model if authenticated, None otherwise
        """
        username = _normalize_optional_text(username)
        if not username:
            return None

        user = await UserService.get_by_username(session, username, auth_type="local")
        if user is None:
            return None

        if not user.is_active:
            return None

        if user.password is None:
            return None

        if not verify_password(password, user.password):
            return None


        # Update last login time
        user.last_login_at = datetime.utcnow()
        await session.flush()

        return user

    @staticmethod
    async def list_all(
        session: AsyncSession,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[UserModel], int]:
        """List all Users with optional filtering.

        Args:
            session: Database session
            is_active: Filter by active status
            search: Search by username or email
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (list of users, total count)
        """
        query = select(UserModel)

        if is_active is not None:
            query = query.where(UserModel.is_active == is_active)

        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    UserModel.username.ilike(search_pattern),
                    UserModel.email.ilike(search_pattern),
                    UserModel.display_name.ilike(search_pattern),
                )
            )

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_query)).scalar()

        # Get paginated results
        query = query.offset((page - 1) * page_size).limit(page_size)
        query = query.order_by(UserModel.created_at.desc())

        result = await session.execute(query)
        users = list(result.scalars().all())

        return users, total

    @staticmethod
    async def update(
        session: AsyncSession, user_id: str, user_data: UserUpdate
    ) -> Optional[UserModel]:
        """Update a User.

        Args:
            session: Database session
            user_id: User ID
            user_data: Update data

        Returns:
            Updated User model or None
        """
        user = await UserService.get_by_id(session, user_id)
        if user is None:
            return None

        update_data = user_data.model_dump(exclude_unset=True)
        update_data.pop("is_admin", None)

        # Hash new password if provided
        if "password" in update_data and update_data["password"]:
            update_data["password"] = hash_password(update_data["password"])
        elif "password" in update_data:
            del update_data["password"]

        if "email" in update_data:
            update_data["email"] = _normalize_optional_text(update_data["email"])
        if "display_name" in update_data:
            update_data["display_name"] = _normalize_optional_text(update_data["display_name"])
        if "auth_type" in update_data:
            update_data["auth_type"] = _normalize_auth_type(update_data["auth_type"])

        for key, value in update_data.items():
            setattr(user, key, value)

        user.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(user)

        logger.info(f"Updated user: {user.username} (id={user.id})")
        return user

    @staticmethod
    async def delete(session: AsyncSession, user_id: str) -> bool:
        """Delete a User.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            True if deleted, False if not found
        """
        user = await UserService.get_by_id(session, user_id)
        if user is None:
            return False

        await session.delete(user)
        logger.info(f"Deleted user: {user.username} (id={user.id})")
        return True

    @staticmethod
    async def update_last_login(session: AsyncSession, user_id: str) -> Optional[UserModel]:
        """Update last login timestamp.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            Updated User model or None
        """
        user = await UserService.get_by_id(session, user_id)
        if user is None:
            return None

        user.last_login_at = datetime.utcnow()
        await session.flush()
        return user

    @staticmethod
    async def count_users_with_role(session: AsyncSession, role_identifier: str) -> int:
        """Count users currently assigned to a role identifier."""
        result = await session.execute(select(UserModel))
        users = result.scalars().all()
        return sum(1 for user in users if _has_role_assignment(user.roles, role_identifier))

    @staticmethod
    def to_user_info(user: UserModel) -> Dict[str, Any]:
        """Convert User model to UserInfo format for auth.

        Args:
            user: User model

        Returns:
            UserInfo dict
        """
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
            "roles": user.roles or {},
            "is_admin": user.is_admin,
            "auth_type": user.auth_type,
        }
