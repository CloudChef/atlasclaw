# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Administrator self-service routes for long-lived opaque API tokens."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.guards import AuthorizationContext, get_authorization_context
from app.atlasclaw.db import get_db_session_dependency as get_db_session
from app.atlasclaw.db.orm.audit import AuditService
from app.atlasclaw.db.orm.user_access_token import UserAccessTokenService
from app.atlasclaw.db.schemas import (
    UserAccessTokenCreate,
    UserAccessTokenCreatedResponse,
    UserAccessTokenListResponse,
    UserAccessTokenResponse,
)


router = APIRouter(prefix="/access-tokens", tags=["Access Token API"])


def _token_management_owner_id(authz: AuthorizationContext) -> str:
    """Return the administrator owner ID after rejecting API-token authentication."""
    if str(authz.user.auth_type or "").strip().lower() == "api_token":
        raise HTTPException(
            status_code=403,
            detail="API tokens cannot access token management",
        )
    if not authz.is_admin or authz.db_user is None:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return authz.db_user.id


def _created_response(model: object, plaintext: str) -> UserAccessTokenCreatedResponse:
    """Serialize one-time plaintext together with safe persisted metadata."""
    metadata = UserAccessTokenResponse.model_validate(model)
    return UserAccessTokenCreatedResponse(**metadata.model_dump(), token=plaintext)


@router.post("", response_model=UserAccessTokenCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_access_token(
    body: UserAccessTokenCreate,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> UserAccessTokenCreatedResponse:
    """Create an opaque token for an administrator not using API-token authentication.

    Args:
        body: User-visible token name; blank names are rejected by schema validation.
        session: Transaction-scoped database session supplied by FastAPI.
        authz: Current authorization context, which must represent an administrator
            authenticated through something other than an API token.

    Returns:
        Persisted token metadata plus the plaintext secret, which is returned only
        by this response and cannot be recovered later.

    Raises:
        HTTPException: With 403 for API-token authentication or a non-administrator.
    """
    owner_id = _token_management_owner_id(authz)
    model, plaintext = await UserAccessTokenService.create(
        session,
        user_id=owner_id,
        name=body.name,
    )
    await AuditService.log_audit(
        session,
        entity_type="user_access_token",
        entity_id=model.id,
        action="CREATE",
        user_id=owner_id,
        new_value={"name": model.name, "token_hint": model.token_hint},
    )
    return _created_response(model, plaintext)


@router.get("", response_model=UserAccessTokenListResponse)
async def list_access_tokens(
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> UserAccessTokenListResponse:
    """List non-secret metadata owned by an administrator not using an API token.

    Args:
        session: Transaction-scoped database session supplied by FastAPI.
        authz: Current authorization context; API-token authentication is rejected.

    Returns:
        Token identifiers, names, hints, timestamps, and revocation state without
        plaintext secrets or stored digests.

    Raises:
        HTTPException: With 403 for API-token authentication or a non-administrator.
    """
    owner_id = _token_management_owner_id(authz)
    models = await UserAccessTokenService.list_for_user(session, owner_id)
    tokens = [UserAccessTokenResponse.model_validate(model) for model in models]
    return UserAccessTokenListResponse(tokens=tokens, total=len(tokens))


@router.delete("/{token_id}", response_model=UserAccessTokenResponse)
async def revoke_access_token(
    token_id: str,
    session: AsyncSession = Depends(get_db_session),
    authz: AuthorizationContext = Depends(get_authorization_context),
) -> UserAccessTokenResponse:
    """Revoke one owned token while retaining its non-secret audit metadata.

    Args:
        token_id: Stable identifier of a token owned by the current administrator.
        session: Transaction-scoped database session supplied by FastAPI.
        authz: Current authorization context; API-token authentication is rejected.

    Returns:
        Updated metadata containing the immediate revocation timestamp.

    Raises:
        HTTPException: With 403 for non-interactive/non-admin callers, or 404 when
            the identifier is not owned by the current administrator.
    """
    owner_id = _token_management_owner_id(authz)
    model = await UserAccessTokenService.revoke(
        session,
        token_id=token_id,
        user_id=owner_id,
    )
    if model is None:
        raise HTTPException(status_code=404, detail="Access token not found")
    await AuditService.log_audit(
        session,
        entity_type="user_access_token",
        entity_id=model.id,
        action="REVOKE",
        user_id=owner_id,
        old_value={"name": model.name, "token_hint": model.token_hint},
    )
    return UserAccessTokenResponse.model_validate(model)
