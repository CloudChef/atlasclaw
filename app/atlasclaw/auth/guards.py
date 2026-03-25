# -*- coding: utf-8 -*-
"""
FastAPI dependency guards for authentication and authorization.

Provides reusable dependency functions for:
- Extracting authenticated user from request state
- Requiring admin privileges for protected endpoints
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.atlasclaw.auth.models import UserInfo


async def get_current_user(request: Request) -> UserInfo:
    """
    Extract authenticated user from request state.

    This dependency retrieves the UserInfo object injected by AuthMiddleware
    and validates that the user is properly authenticated (not anonymous).

    Args:
        request: The FastAPI request object

    Returns:
        UserInfo: The authenticated user's information

    Raises:
        HTTPException: 401 if no user info found or user is anonymous
    """
    user_info = getattr(request.state, "user_info", None)
    if not user_info or user_info.user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_info


async def require_admin(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """
    Require admin privileges for the current user.

    This dependency builds on get_current_user and additionally checks
    that the authenticated user has admin privileges.

    Args:
        user: The authenticated user (injected by get_current_user)

    Returns:
        UserInfo: The authenticated admin user's information

    Raises:
        HTTPException: 401 if not authenticated (via get_current_user)
        HTTPException: 403 if user is not an admin
    """
    is_admin = user.extra.get("is_admin", False) if user.extra else False
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
