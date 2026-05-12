# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""

Configuration manager

implementmulti configurationparse(run > environment variable > > default value)andconfiguration.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Optional, Any
from pydantic import ValidationError
from dotenv import load_dotenv

from app.atlasclaw.core.config_schema import AtlasClawConfig
from app.atlasclaw.core.user_paths import user_runtime_dir


class ConfigManager:
    """

Configuration manager
    
    configuration(from to):
    1. runtime override(through set())
    2. environment variable(ATLASCLAW_* prefix)
    3. profile(atlasclaw.json / atlasclaw.yaml)
    4. default value(config_schema.py in)
    
    Example usage:
        ```python
        config_manager = ConfigManager()
        config_manager.load()
        
        # getconfiguration
        timeout = config_manager.config.agent_defaults.timeout_seconds
        
        # runtime override
        config_manager.set("agent_defaults.timeout_seconds", 300)
        
        #
        config_manager.reload()
        ```
    
"""
    
    DEFAULT_CONFIG_PATHS = [
        "atlasclaw.json",
        "atlasclaw.yaml",
        "~/.atlasclaw/config.json",
    ]
    
    ENV_PREFIX = "ATLASCLAW_"
    
    def __init__(self, config_path: Optional[str] = None):
        """

initializeConfiguration manager
        
        Args:
            config_path:configurationfile path(optional)
        
"""
        self._config_path = config_path or os.environ.get("ATLASCLAW_CONFIG")
        self._config: AtlasClawConfig = AtlasClawConfig()
        self._runtime_overrides: dict[str, Any] = {}
        self._loaded = False
        self._resolved_config_path: Optional[Path] = None
    
    @property
    def config(self) -> AtlasClawConfig:
        """get configuration"""
        if not self._loaded:
            self.load()
        return self._config

    @property
    def resolved_config_path(self) -> Optional[Path]:
        """Return the absolute path of the config file that was loaded, if any."""
        if not self._loaded:
            self.load()
        return self._resolved_config_path
    
    def load(self) -> AtlasClawConfig:
        """
        Load configuration.
        
        Priority: defaults <- global config <- workspace config <- env vars <- runtime overrides
        
        Returns:
            Configuration object
        """
        # 1. Start from defaults
        config_dict: dict[str, Any] = {}
        
        # 2. Load global config
        global_config = self._load_from_file()
        if global_config:
            config_dict = self._deep_merge(config_dict, global_config)
        
        # 3. Load workspace config (highest priority file config)
        workspace_config = self._load_workspace_config()
        if workspace_config:
            config_dict = self._deep_merge(config_dict, workspace_config)
        
        # 4. Load from environment variables
        env_config = self._load_from_env()
        if env_config:
            config_dict = self._deep_merge(config_dict, env_config)
        
        # 5. Apply runtime overrides
        if self._runtime_overrides:
            config_dict = self._deep_merge(config_dict, self._runtime_overrides)
        
        # 6. Expand environment variable placeholders in config dict
        config_dict = self._expand_env_vars(config_dict)
        
        # 7. Create configuration object
        try:
            self._config = AtlasClawConfig(**config_dict)
        except ValidationError as e:
            # Config validation failed, use defaults
            print(f"[ConfigManager] Config validation failed, using defaults: {e}")
            self._config = AtlasClawConfig()
        
        self._loaded = True
        return self._config
    
    def _expand_env_vars(self, obj: Any) -> Any:
        """Recursively expand environment variable placeholders and decrypt encrypted values in config.

        Placeholders in format ${VAR_NAME} are replaced with environment variable values.
        Encrypted values in format enc:v1:... are decrypted using the encryption service.
        If the env var is not set, returns empty string for string fields (allows Pydantic
        validation to pass) or None for other types.
        """
        from app.atlasclaw.core.encryption import decrypt, FORMAT_PREFIX

        if isinstance(obj, str):
            # Check for encrypted value format: enc:v1:base64payload
            if obj.startswith("enc:") and FORMAT_PREFIX in obj:
                try:
                    encrypted_payload = obj[4:]  # Remove 'enc:' prefix
                    return decrypt(encrypted_payload)
                except Exception as e:
                    print(f"[ConfigManager] Failed to decrypt encrypted value: {e}")
                    return obj  # Return original if decryption fails

            # Check for environment variable placeholder
            if obj.startswith("${") and obj.endswith("}"):
                var_name = obj[2:-1]
                # Return env var value or empty string if not set
                return os.environ.get(var_name, "")
            return obj
        elif isinstance(obj, dict):
            return {k: self._expand_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._expand_env_vars(item) for item in obj]
        return obj
    
    def _load_workspace_config(self) -> Optional[dict]:
        """Load workspace configuration."""
        # First try to get workspace path from loaded config
        workspace_path = "."
        if self._config_path:
            workspace_path = str(Path(self._config_path).parent)
        
        # Try to load workspace atlasclaw.json
        workspace_config_path = Path(workspace_path) / "atlasclaw.json"
        if workspace_config_path.exists():
            try:
                with open(workspace_config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ConfigManager] Failed to read workspace config {workspace_config_path}: {e}")
        
        return None
    
    def load_user_config(self, user_id: str) -> dict:
        """Load user-specific configuration.
        
        Loads user config from users/<user_id>/user_setting.json.
        Backward compatible: if user_setting.json doesn't exist, try atlasclaw.json.
        
        Args:
            user_id: User identifier
            
        Returns:
            User config dict with channels, providers, preferences fields
        """
        workspace_path = Path(self.config.workspace.path)
        user_dir = user_runtime_dir(workspace_path, user_id)
        
        # First try to load user_setting.json (new format)
        user_config_path = user_dir / "user_setting.json"
        if user_config_path.exists():
            try:
                with open(user_config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ConfigManager] Failed to read user config {user_config_path}: {e}")
        
        # Backward compatible: try loading old atlasclaw.json
        legacy_config_path = user_dir / "atlasclaw.json"
        if legacy_config_path.exists():
            try:
                with open(legacy_config_path, "r", encoding="utf-8") as f:
                    legacy_config = json.load(f)
                    # Convert to new format
                    return {
                        "channels": legacy_config.get("channels", {}),
                        "providers": legacy_config.get("providers", {}),
                        "preferences": legacy_config.get("preferences", {}),
                    }
            except Exception as e:
                print(f"[ConfigManager] Failed to read legacy user config {legacy_config_path}: {e}")
        
        return {}
    
    def reload(self) -> AtlasClawConfig:
        """configuration"""
        self._loaded = False
        return self.load()
    
    def set(self, key: str, value: Any) -> None:
        """

run configuration
        
        Args:
            key:configuration(, such as "agent_defaults.timeout_seconds")
            value:configuration
        
"""
        self._set_nested(self._runtime_overrides, key.split("."), value)
        # apply
        self._loaded = False
    
    def get(self, key: str, default: Any = None) -> Any:
        """

get configuration
        
        Args:
            key:configuration()
            default:default value
            
        Returns:
            configuration ordefault value
        
"""
        try:
            obj = self.config
            for part in key.split("."):
                if hasattr(obj, part):
                    obj = getattr(obj, part)
                elif isinstance(obj, dict) and part in obj:
                    obj = obj[part]
                else:
                    return default
            return obj
        except Exception:
            return default
    
    def _load_from_file(self) -> Optional[dict]:
        """from configuration"""
        paths = [self._config_path] if self._config_path else self.DEFAULT_CONFIG_PATHS
        
        for path_str in paths:
            if not path_str:
                continue
            path = Path(path_str).expanduser()
            if path.exists():
                try:
                    self._resolved_config_path = path.resolve()
                    self._load_sidecar_dotenv(self._resolved_config_path)
                    with open(path, "r", encoding="utf-8") as f:
                        if path.suffix == ".json":
                            return json.load(f)
                        elif path.suffix in (".yaml", ".yml"):
                            # YAML support(optional)
                            try:
                                import yaml
                                return yaml.safe_load(f)
                            except ImportError:
                                print(f"[ConfigManager] YAML support requires PyYAML installation")
                                continue
                except Exception as e:
                    print(f"[ConfigManager] Failed to read config file {path}: {e}")
                    continue
        return None

    @staticmethod
    def _load_sidecar_dotenv(config_path: Path) -> None:
        """Load `.env` next to the resolved config file without overriding existing env vars."""
        dotenv_path = config_path.parent / ".env"
        if dotenv_path.is_file():
            load_dotenv(dotenv_path=dotenv_path, override=False)
    
    def _load_from_env(self) -> dict:
        """

fromenvironment variable configuration
        
        environment variableformat:ATLASCLAW_<PATH>__<KEY>
        such as:ATLASCLAW_AGENT_DEFAULTS__TIMEOUT_SECONDS=300
        
"""
        config: dict[str, Any] = {}
        
        for key, value in os.environ.items():
            if not key.startswith(self.ENV_PREFIX):
                continue
            
            # prefix configuration
            config_key = key[len(self.ENV_PREFIX):].lower()
            parts = config_key.split("__")
            
            # parse type
            parsed_value = self._parse_env_value(value)
            
            # to configurationdictionary
            self._set_nested(config, parts, parsed_value)
        
        return config
    
    def _parse_env_value(self, value: str) -> Any:
        """parseenvironment variable type"""
        # parse JSON(support type)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
        
        # 
        if value.lower() in ("true", "yes", "1"):
            return True
        if value.lower() in ("false", "no", "0"):
            return False
        
        # count
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        
        # characters
        return value
    
    def _set_nested(self, d: dict, keys: list[str], value: Any) -> None:
        """at dictionaryin"""
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = value
    
    def _deep_merge(self, base: dict, override: dict) -> dict:
        """dictionary"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


# Configuration managerinstance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """get Configuration managerinstance"""
    global _config_manager
    config_path = os.environ.get("ATLASCLAW_CONFIG")
    if _config_manager is None:
        _config_manager = ConfigManager(config_path=config_path)
    elif (_config_manager._config_path or None) != (config_path or None):
        # Rebuild the singleton when tests or runtime update ATLASCLAW_CONFIG.
        _config_manager = ConfigManager(config_path=config_path)
    return _config_manager


def get_config() -> AtlasClawConfig:
    """get configuration"""
    return get_config_manager().config


def get_config_path() -> Optional[Path]:
    """Return the resolved config file path for the active config manager."""
    return get_config_manager().resolved_config_path
