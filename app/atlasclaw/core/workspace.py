# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Workspace initialization and management.

This module provides workspace directory structure initialization
and management for AtlasClaw.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from app.atlasclaw.core.user_paths import normalize_runtime_user_id, user_runtime_dir


class WorkspaceInitializer:
    """Initialize and manage workspace directory structure.
    
    The workspace directory (default: ./.atlasclaw, configurable) contains ONLY user
    runtime data and the core configuration file. Provider packages and standalone
    skill packages are stored outside the workspace via `providers_root` and
    `skills_root`; channel handlers live in the application source tree.
    
    Directory structure:
    <workspace>/                 (default: ./.atlasclaw)
    ├── atlasclaw.json           # Core configuration
    ├── agents/                  # Agent definitions
    └── users/                   # User data only
    """
    
    def __init__(self, workspace_path: str = "./.atlasclaw"):
        """Initialize workspace initializer.
        
        Args:
            workspace_path: Path to the workspace root directory (default: ./.atlasclaw).
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.users_dir = self.workspace_path / "users"
        self._main_agent_filenames = ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md")
        self._runtime_state_filename = "runtime_state.json"
    
    def initialize(self) -> bool:
        """Initialize workspace directory structure.
        
        Creates the following structure:
        <workspace>/                 (default: ./.atlasclaw, configurable)
        ├── agents/                  # Agent definitions
        └── users/                   # User data only
        
        Note: standalone skills are external via `skills_root`, while channel
        handlers live in `app/atlasclaw/channels`.
        
        Returns:
            True if initialization was successful.
        """
        try:
            # Create workspace directory structure
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            (self.workspace_path / "agents").mkdir(exist_ok=True)
            # Standalone skills are external; channel handlers live in the app source.
            
            # Create users directory inside workspace
            self.users_dir.mkdir(exist_ok=True)
            
            # Create or update the default main agent from templates.
            self._sync_default_main_agent()
            
            return True
        except Exception as e:
            print(f"[WorkspaceInitializer] Failed to initialize workspace: {e}")
            return False
    
    def _sync_default_main_agent(self) -> None:
        """Synchronize default main agent files from repo templates.

        Synchronization state is stored in `<workspace>/runtime_state.json`
        under `template_sync`. Files that already match the template only update
        the hash baseline. Existing installations without a hash baseline are
        overwritten once when their content differs from the template. After a
        baseline exists, files changed by users are left untouched.
        """
        main_agent_dir = self.workspace_path / "agents" / "main"
        main_agent_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_runtime_state()
        template_sync = state.setdefault("template_sync", {})
        template_dir = self._get_default_main_agent_template_dir()
        for filename in self._main_agent_filenames:
            relative_path = f"agents/main/{filename}"
            template_path = template_dir / filename
            target_path = main_agent_dir / filename
            record = template_sync.get(relative_path)

            should_overwrite = self._should_overwrite_template_target(
                template_path=template_path,
                target_path=target_path,
                record=record,
            )
            if should_overwrite:
                shutil.copy2(template_path, target_path)

            if target_path.exists() and (
                should_overwrite or self._hash_file(target_path) == self._hash_file(template_path)
            ):
                template_sync[relative_path] = {
                    "template_hash": self._hash_file(template_path),
                    "target_hash": self._hash_file(target_path),
                }

        self._save_runtime_state(state)

    def _load_runtime_state(self) -> dict[str, Any]:
        """Load workspace runtime state."""
        state_path = self.workspace_path / self._runtime_state_filename
        if not state_path.exists():
            return {"version": 1, "template_sync": {}}
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"version": 1, "template_sync": {}}
        if not isinstance(raw, dict):
            return {"version": 1, "template_sync": {}}
        raw.setdefault("version", 1)
        template_sync = raw.get("template_sync")
        if not isinstance(template_sync, dict):
            raw["template_sync"] = {}
        return raw

    def _save_runtime_state(self, state: dict[str, Any]) -> None:
        """Save workspace runtime state."""
        state_path = self.workspace_path / self._runtime_state_filename
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _should_overwrite_template_target(
        self,
        *,
        template_path: Path,
        target_path: Path,
        record: object,
    ) -> bool:
        """Return whether a workspace template target should be overwritten."""
        if not target_path.exists():
            return True

        template_hash = self._hash_file(template_path)
        target_hash = self._hash_file(target_path)
        if target_hash == template_hash:
            return False

        if not isinstance(record, dict):
            return True

        previous_target_hash = record.get("target_hash")
        if not isinstance(previous_target_hash, str):
            return True
        return target_hash == previous_target_hash

    @staticmethod
    def _hash_file(path: Path) -> str:
        """Return a stable SHA-256 hash for a file."""
        return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"

    def _create_default_main_agent(self) -> None:
        """Compatibility wrapper for older callers."""
        self._sync_default_main_agent()

    def _get_default_main_agent_template_dir(self) -> Path:
        """Return the repo template directory for the default main agent."""
        return Path(__file__).resolve().parent.parent / "templates" / "agents" / "main"

    def is_initialized(self) -> bool:

        """Check if workspace is initialized.
        
        Note: standalone skills are external and channel handlers live in the app source.
        """
        return (
            self.workspace_path.exists()
            and (self.workspace_path / "agents").exists()
            and self.users_dir.exists()
            and self._default_main_agent_exists()
        )

    def _default_main_agent_exists(self) -> bool:
        """Return True when the default main agent directory contains all required files."""
        main_agent_dir = self.workspace_path / "agents" / "main"
        return all((main_agent_dir / filename).exists() for filename in self._main_agent_filenames)


class UserWorkspaceInitializer:
    """Initialize and manage user-specific workspace directories."""
    
    def __init__(self, workspace_path: str, user_id: str):
        """Initialize user workspace initializer.
        
        Args:
            workspace_path: Path to the workspace root directory.
            user_id: User identifier.
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.user_id = user_id
        self.storage_user_id = normalize_runtime_user_id(user_id)
        self.user_dir = user_runtime_dir(self.workspace_path, user_id)
    
    def initialize(self) -> bool:
        """Initialize user directory structure.
        
        Creates the following structure:
        users/<user-id>/
        ├── user_setting.json       # User-specific configs (renamed from atlasclaw.json)
        ├── sessions/               # JSONL session transcripts
        └── memory/                 # Markdown memory files
        
        Note: channels/ is no longer created; user-level channel configs are stored
        in user_setting.json instead.
        
        Returns:
            True if initialization was successful.
        """
        try:
            # Create user directory structure
            self.user_dir.mkdir(parents=True, exist_ok=True)
            # channels/ is no longer created; user-level channel configs go in user_setting.json
            (self.user_dir / "sessions").mkdir(exist_ok=True)
            (self.user_dir / "memory").mkdir(exist_ok=True)
            (self.user_dir / "work_dir").mkdir(exist_ok=True)
            
            # Create default user config if not exists
            self._create_default_user_config()
            
            return True
        except Exception as e:
            print(f"[UserWorkspaceInitializer] Failed to initialize user workspace: {e}")
            return False
    
    def _create_default_user_config(self) -> None:
        """Create default user_setting.json if it doesn't exist."""
        user_config_path = self.user_dir / "user_setting.json"
        if user_config_path.exists():
            return
        
        default_config = {
            "channels": {},       # User-level channel configs (Feishu bot, etc.)
            "providers": {},      # User-level provider credentials bound to system templates
            "preferences": {}     # User preferences (language, timezone, etc.)
        }
        
        try:
            with open(user_config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[UserWorkspaceInitializer] Failed to create user config: {e}")
    
    def is_initialized(self) -> bool:
        """Check if user workspace is initialized.
        
        Note: channels/ is no longer required as user-level channel configs
        are stored in user_setting.json.
        """
        return (
            self.user_dir.exists()
            and (self.user_dir / "sessions").exists()
            and (self.user_dir / "memory").exists()
            and (self.user_dir / "work_dir").exists()
        )
    
    def get_sessions_dir(self) -> Path:
        """Get user sessions directory."""
        return self.user_dir / "sessions"
    
    def get_memory_dir(self) -> Path:
        """Get user memory directory."""
        return self.user_dir / "memory"
