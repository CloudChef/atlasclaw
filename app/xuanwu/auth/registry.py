# -*- coding: utf-8 -*-
"""Auth registry for managing authentication providers."""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from .provider import AuthProvider

logger = logging.getLogger(__name__)


class AuthRegistry:
    """Registry for authentication providers.
    
    Manages built-in and extension auth providers.
    """
    
    _providers: Dict[str, Type[AuthProvider]] = {}
    
    @classmethod
    def register(cls, auth_id: str, provider_class: Type[AuthProvider]) -> None:
        """Register an auth provider class.
        
        Args:
            auth_id: Unique auth provider identifier
            provider_class: Auth provider class
        """
        if not issubclass(provider_class, AuthProvider):
            raise ValueError(f"Provider must inherit from AuthProvider: {provider_class}")
        
        cls._providers[auth_id] = provider_class
        logger.info(f"Registered auth provider: {auth_id}")
    
    @classmethod
    def get(cls, auth_id: str) -> Optional[Type[AuthProvider]]:
        """Get auth provider class by ID.
        
        Args:
            auth_id: Auth provider identifier
            
        Returns:
            Provider class or None if not found
        """
        return cls._providers.get(auth_id)
    
    @classmethod
    def list_providers(cls) -> List[Dict[str, Any]]:
        """List all registered auth providers.
        
        Returns:
            List of provider info dicts with id, name
        """
        result = []
        for auth_id, provider_class in cls._providers.items():
            result.append({
                "id": auth_id,
                "name": provider_class.auth_name or auth_id,
            })
        return result
    
    @classmethod
    def scan_providers(cls, providers_dir: Path) -> None:
        """Scan providers directory for auth extensions.
        
        Args:
            providers_dir: Path to providers directory
        """
        if not providers_dir.exists():
            logger.warning(f"Providers directory not found: {providers_dir}")
            return
        
        for provider_dir in providers_dir.iterdir():
            if not provider_dir.is_dir():
                continue
            
            auth_dir = provider_dir / "auth"
            if not auth_dir.exists():
                continue
            
            cls._scan_auth_extensions(auth_dir)
    
    @classmethod
    def _scan_auth_extensions(cls, auth_dir: Path) -> None:
        """Scan auth extensions in a directory.
        
        Args:
            auth_dir: Path to auth directory
        """
        for file_path in auth_dir.glob("*.py"):
            if file_path.name.startswith("_"):
                continue
            
            try:
                module = cls._import_module_from_path(file_path)
                cls._register_providers_from_module(module)
            except Exception as e:
                logger.error(f"Failed to load auth extension {file_path}: {e}")
    
    @classmethod
    def _import_module_from_path(cls, file_path: Path) -> Any:
        """Import a module from file path.
        
        Args:
            file_path: Path to Python file
            
        Returns:
            Imported module
        """
        spec = importlib.util.spec_from_file_location(
            file_path.stem, file_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    
    @classmethod
    def _register_providers_from_module(cls, module: Any) -> None:
        """Register auth providers from a module.
        
        Args:
            module: Imported module
        """
        for name, obj in inspect.getmembers(module):
            if (inspect.isclass(obj) and 
                issubclass(obj, AuthProvider) and 
                obj is not AuthProvider and
                hasattr(obj, 'auth_id') and
                obj.auth_id):
                cls.register(obj.auth_id, obj)
