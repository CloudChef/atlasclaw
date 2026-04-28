# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
AuthStrategy — orchestrates Provider → DB-backed user resolution → UserInfo.
Includes a simple in-memory TTL cache keyed by credential.

Supports chained authentication: tries multiple providers in sequence,
accepting the first successful authentication.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.user_store import resolve_user_info_for_auth_result
from app.atlasclaw.auth.models import AuthResult, AuthenticationError, UserInfo
from app.atlasclaw.auth.providers.base import AuthProvider
from app.atlasclaw.core.workspace import UserWorkspaceInitializer

logger = logging.getLogger(__name__)


class AuthStrategy:
    """
    Coordinates the full authentication flow with multi-provider support.
    
    Authentication flow:
      1. Try primary provider (local/none)
      2. If primary fails, try secondary providers (oidc_jwt, etc.)
      3. First successful AuthResult → DB user/UserInfo
      
    Results are cached in memory for ``cache_ttl_seconds`` to avoid repeated
    remote calls for the same token.
    """

    def __init__(
        self,
        providers: list[AuthProvider],
        workspace_path: str = ".",
        cache_ttl_seconds: int = 300,
    ) -> None:
        """
        Args:
            providers: List of providers to try in order
            workspace_path: Workspace root for per-user runtime directories
            cache_ttl_seconds: Cache TTL in seconds
        """
        self._providers = providers
        self._workspace_path = workspace_path
        self._cache_ttl = cache_ttl_seconds
        # token -> (UserInfo, expiry_monotonic_ts)
        self._cache: dict[str, tuple[UserInfo, float]] = {}

    @property
    def providers(self) -> list[AuthProvider]:
        """Get all configured providers."""
        return self._providers.copy()
    
    @property
    def primary_provider(self) -> AuthProvider | None:
        """Get the first (primary) provider."""
        return self._providers[0] if self._providers else None

    def _ensure_user_workspace(self, user_id: str) -> None:
        user_initializer = UserWorkspaceInitializer(
            str(self._workspace_path),
            user_id,
        )
        user_initializer.initialize()

    def ensure_user_workspace(self, user_id: str) -> None:
        self._ensure_user_workspace(user_id)

    async def resolve_user_from_auth_result(
        self,
        *,
        provider: str,
        result: AuthResult,
        raw_token: str = "",
        extra: Optional[dict] = None,
        auth_type: str = "",
    ) -> UserInfo:
        """Resolve an AuthResult to UserInfo and initialize runtime workspace."""
        user_info = await resolve_user_info_for_auth_result(
            provider=provider,
            result=result,
            raw_token=raw_token or result.raw_token,
            extra=extra,
            auth_type=auth_type,
        )
        self._ensure_user_workspace(user_info.user_id)
        return user_info

    async def resolve_user(self, credential: str) -> UserInfo:
        """
        Validate *credential* against all providers and return UserInfo.
        
        Tries providers in order until one succeeds.
        
        Args:
            credential: Authentication credential (token, password, etc.)
            
        Returns:
            UserInfo for authenticated user
            
        Raises:
            AuthenticationError: If all providers fail
        """
        # --- TTL cache hit -------------------------------------------
        if credential in self._cache:
            user_info, expiry = self._cache[credential]
            if time.monotonic() < expiry:
                self._ensure_user_workspace(user_info.user_id)
                logger.debug("Auth cache hit")
                return user_info
            del self._cache[credential]

        # --- Try providers in sequence -------------------------------
        last_error: AuthenticationError | None = None
        
        for provider in self._providers:
            try:
                logger.debug(f"Trying provider: {provider.provider_name()}")
                result = await provider.authenticate(credential)

                provider_name = provider.provider_name()
                if provider_name in {"none", "local"}:
                    result_extra = dict(result.extra or {})
                    auth_type = result_extra.get("auth_type", provider_name)
                    db_user_id = str(result_extra.get("db_user_id", "") or "").strip()
                    runtime_user_id = (
                        db_user_id
                        if provider_name == "local" and auth_type != "local" and db_user_id
                        else result.subject
                    )
                    user_info = UserInfo(
                        user_id=runtime_user_id,
                        display_name=result.display_name or result.subject,
                        tenant_id=result.tenant_id,
                        roles=list(result.roles),
                        raw_token=result.raw_token,
                        provider_subject=f"{provider_name}:{result.subject}",
                        extra=result_extra,
                        auth_type=auth_type,
                    )
                    self._ensure_user_workspace(user_info.user_id)
                else:
                    user_info = await self.resolve_user_from_auth_result(
                        provider=provider_name,
                        result=result,
                        raw_token=result.raw_token,
                        extra=dict(result.extra or {}),
                    )

                if self._cache_ttl > 0:
                    self._cache[credential] = (
                        user_info,
                        time.monotonic() + self._cache_ttl,
                    )

                logger.info(
                    f"Authentication succeeded: provider={provider_name}, "
                    f"user={user_info.user_id}"
                )
                return user_info
                
            except AuthenticationError as e:
                logger.debug(f"Provider {provider.provider_name()} failed: {e}")
                last_error = e
                # Continue to next provider
                continue
        
        # All providers failed
        logger.warning(f"All authentication providers failed for credential")
        raise last_error or AuthenticationError("Authentication failed")


def create_auth_strategy(
    config: Optional[AuthConfig],
    workspace_path: str = ".",
) -> Optional[AuthStrategy]:
    """
    Factory that builds an AuthStrategy with multi-provider support.
    
    By default, creates a chained strategy:
      1. Primary provider (local/none from config)
      2. OIDC JWT validator (for external IdP tokens)
    
    This allows users to authenticate via:
      - Local username/password (database)
      - OIDC JWT tokens (external IdP like Keycloak)
    
    Args:
        config: Auth configuration from atlasclaw.json
        workspace_path: Workspace root for per-user runtime directories
        
    Returns:
        AuthStrategy instance, or None if config is None (anonymous mode)
    """
    if config is None:
        logger.info("Auth disabled (config is None)")
        return None

    from app.atlasclaw.auth.providers import (
        create_local_provider,
        get_jwt_validator,
    )

    try:
        config.validate_provider_config()
    except ValueError as exc:
        logger.error("Auth config validation failed: %s", exc)
        raise

    providers: list[AuthProvider] = []
    
    # 1. Primary provider (local/none)
    primary = create_local_provider(config)
    providers.append(primary)
    logger.info(f"Primary auth provider: {primary.provider_name()}")
    
    # 2. OIDC JWT validator (for external IdP tokens)
    # Always add JWT validator if OIDC config is present
    if config.oidc.issuer or config.oidc.jwks_uri:
        oidc_cfg = config.oidc.expanded()
        jwt_validator = get_jwt_validator(
            issuer=oidc_cfg.issuer,
            client_id=oidc_cfg.client_id,
            jwks_uri=oidc_cfg.jwks_uri,
        )
        providers.append(jwt_validator)
        logger.info(f"Added JWT validator: issuer={oidc_cfg.issuer or 'auto-discover'}")
    else:
        # Even without explicit config, add a JWT validator with empty config
        # This allows auto-discovery from token's 'iss' claim
        jwt_validator = get_jwt_validator()
        providers.append(jwt_validator)
        logger.info("Added JWT validator with auto-discovery")

    return AuthStrategy(
        providers=providers,
        workspace_path=workspace_path,
        cache_ttl_seconds=config.cache_ttl_seconds,
    )
