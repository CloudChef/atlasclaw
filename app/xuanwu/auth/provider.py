# -*- coding: utf-8 -*-
"""Auth provider abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class AuthProvider(ABC):
    """Abstract base class for authentication providers.
    
    Built-in providers (OIDC, OAuth2, API Key, SAML, None) and extension
    providers (LDAP, etc.) must implement this interface.
    """
    
    # Class attributes - must be defined by subclasses
    auth_id: str = ""
    auth_name: str = ""
    
    @abstractmethod
    async def authenticate(self, credentials: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Authenticate user with credentials.
        
        Args:
            credentials: Authentication credentials (token, api_key, etc.)
            
        Returns:
            User info dict if authenticated, None otherwise
            User info must contain: id, email, name, provider
        """
        pass
    
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier.
        
        Returns:
            Provider ID string (e.g., "oidc", "ldap")
        """
        pass
    
    async def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate provider configuration.
        
        Args:
            config: Provider configuration
            
        Returns:
            True if configuration is valid
        """
        return True
    
    def describe_schema(self) -> Dict[str, Any]:
        """Return configuration schema for UI form generation.
        
        Returns:
            JSON Schema describing required configuration fields
        """
        return {
            "type": "object",
            "properties": {},
        }
