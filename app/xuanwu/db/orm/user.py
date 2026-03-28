# -*- coding: utf-8 -*-
"""Service operations for User configuration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import bcrypt
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.xuanwu.db.models import UserModel
from app.xuanwu.db.schemas import UserCreate, UserUpdate

logger = logging.getLogger(__name__)


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
        # Hash password if provided
        password = None
        if user_data.password:
            password = hash_password(user_data.password)

        user = UserModel(
            username=user_data.username,
            email=user_data.email,
            password=password,
            auth_type=getattr(user_data, "auth_type", "local") or "local",
            display_name=user_data.display_name,
            roles=user_data.roles,
            is_active=user_data.is_active,
            is_admin=user_data.is_admin,
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
        result = await session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_username(session: AsyncSession, username: str) -> Optional[UserModel]:
        """Get User by username.

        Args:
            session: Database session
            username: Username

        Returns:
            User model or None
        """
        result = await session.execute(
            select(UserModel).where(UserModel.username == username)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> Optional[UserModel]:
        """Get User by email.

        Args:
            session: Database session
            email: Email address

        Returns:
            User model or None
        """
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
        user = await UserService.get_by_username(session, username)
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

        # Hash new password if provided
        if "password" in update_data and update_data["password"]:
            update_data["password"] = hash_password(update_data["password"])
        elif "password" in update_data:
            del update_data["password"]


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

