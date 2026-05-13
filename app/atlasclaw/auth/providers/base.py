# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Abstract base class for all AuthProviders."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from app.atlasclaw.auth.models import AuthResult


class AuthProvider(ABC):
    """
    Interface that every authentication backend must implement.

    Implementations must fully validate the credential (not merely check its
    format or presence) before returning an AuthResult.
    """

    auth_id: str = ""
    auth_name: str = ""

    @abstractmethod
    async def authenticate(self, credential: str) -> AuthResult:
        """
        Validate *credential* against the external identity source.

        Returns:
            AuthResult on success.

        Raises:
            AuthenticationError: on any validation failure.
        """
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """Return a short stable identifier, e.g. 'custom', 'oidc', 'none'."""
        ...

    async def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate provider configuration."""
        return True

    def describe_schema(self) -> Dict[str, Any]:
        """Return configuration schema for UI form generation."""
        return {
            "type": "object",
            "properties": {},
        }
