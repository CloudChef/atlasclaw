# -*- coding: utf-8 -*-
"""Provider scanner for loading extensions from providers directory."""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.xuanwu.auth.registry import AuthRegistry
from app.xuanwu.channels.registry import ChannelRegistry
from app.xuanwu.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class ProviderScanner:
    """Scanner for loading auth, channel, and skill extensions from providers."""

    @classmethod
    def scan_providers(cls, providers_dir: Path) -> Dict[str, Any]:
        """Scan providers directory for all extension types.

        Args:
            providers_dir: Path to providers directory

        Returns:
            Dictionary with scan results
        """
        results = {
            "auth": [],
            "channels": [],
            "skills": [],
            "errors": [],
        }

        if not providers_dir.exists():
            logger.warning(f"Providers directory not found: {providers_dir}")
            return results

        for provider_dir in providers_dir.iterdir():
            if not provider_dir.is_dir():
                continue

            provider_name = provider_dir.name
            logger.info(f"Scanning provider: {provider_name}")

            # Load provider config if exists
            provider_config = cls._load_provider_config(provider_dir)

            # Scan auth extensions
            auth_dir = provider_dir / "auth"
            if auth_dir.exists():
                auth_results = cls._scan_auth_extensions(auth_dir)
                results["auth"].extend(auth_results)

            # Scan channel extensions
            channels_dir = provider_dir / "channels"
            if channels_dir.exists():
                channel_results = cls._scan_channel_extensions(channels_dir)
                results["channels"].extend(channel_results)

            # Scan skill extensions
            skills_dir = provider_dir / "skills"
            if skills_dir.exists():
                try:
                    # Use existing SkillRegistry method
                    # SkillRegistry.load_from_directory(skills_dir)
                    results["skills"].append(str(skills_dir))
                except Exception as e:
                    results["errors"].append(f"Skills scan failed for {provider_name}: {e}")

        logger.info(f"Provider scan complete: {results}")
        return results

    @classmethod
    def _load_provider_config(cls, provider_dir: Path) -> Optional[Dict[str, Any]]:
        """Load provider configuration from config.json.

        Args:
            provider_dir: Path to provider directory

        Returns:
            Provider configuration or None
        """
        config_path = provider_dir / "config.json"
        if not config_path.exists():
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load provider config {config_path}: {e}")
            return None

    @classmethod
    def _scan_auth_extensions(cls, auth_dir: Path) -> list:
        """Scan auth extensions in a directory.

        Args:
            auth_dir: Path to auth directory

        Returns:
            List of loaded auth provider IDs
        """
        loaded = []
        for file_path in auth_dir.glob("*.py"):
            if file_path.name.startswith("_"):
                continue

            try:
                module = cls._import_module_from_path(file_path)
                cls._register_auth_providers_from_module(module)
                loaded.append(file_path.stem)
            except Exception as e:
                logger.error(f"Failed to load auth extension {file_path}: {e}")

        return loaded

    @classmethod
    def _scan_channel_extensions(cls, channels_dir: Path) -> list:
        """Scan channel extensions in a directory.

        Args:
            channels_dir: Path to channels directory

        Returns:
            List of loaded channel type IDs
        """
        loaded = []
        for file_path in channels_dir.glob("*.py"):
            if file_path.name.startswith("_"):
                continue

            try:
                module = cls._import_module_from_path(file_path)
                cls._register_channel_handlers_from_module(module)
                loaded.append(file_path.stem)
            except Exception as e:
                logger.error(f"Failed to load channel extension {file_path}: {e}")

        return loaded

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
    def _register_auth_providers_from_module(cls, module: Any) -> None:
        """Register auth providers from a module.

        Args:
            module: Imported module
        """
        import inspect
        from app.xuanwu.auth.provider import AuthProvider

        for name, obj in inspect.getmembers(module):
            if (inspect.isclass(obj) and
                issubclass(obj, AuthProvider) and
                obj is not AuthProvider and
                hasattr(obj, 'auth_id') and
                obj.auth_id):
                AuthRegistry.register(obj.auth_id, obj)

    @classmethod
    def _register_channel_handlers_from_module(cls, module: Any) -> None:
        """Register channel handlers from a module.

        Args:
            module: Imported module
        """
        import inspect
        from app.xuanwu.channels.handler import ChannelHandler

        for name, obj in inspect.getmembers(module):
            if (inspect.isclass(obj) and
                issubclass(obj, ChannelHandler) and
                obj is not ChannelHandler and
                hasattr(obj, 'channel_type') and
                obj.channel_type):
                ChannelRegistry.register(obj.channel_type, obj)
