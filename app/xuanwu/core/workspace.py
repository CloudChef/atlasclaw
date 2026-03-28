"""Workspace initialization and management.

This module provides workspace directory structure initialization
and management for Xuanwu.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


class WorkspaceInitializer:
    """Initialize and manage workspace directory structure.
    
    The workspace directory (default: ./.xuanwu, configurable) contains ONLY user
    runtime data and the core configuration file. Code/config directories (providers,
    skills, channels) are stored in sibling directories configured via *_root settings.
    
    Directory structure:
    <workspace>/                 (default: ./.xuanwu)
    ├── xuanwu.json           # Core configuration
    ├── agents/                  # Agent definitions
    └── users/                   # User data only
    """
    
    def __init__(self, workspace_path: str = "./.xuanwu"):
        """Initialize workspace initializer.
        
        Args:
            workspace_path: Path to the workspace root directory (default: ./.xuanwu).
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.users_dir = self.workspace_path / "users"
        self._main_agent_filenames = ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md")
    
    def initialize(self) -> bool:
        """Initialize workspace directory structure.
        
        Creates the following structure:
        <workspace>/                 (default: ./.xuanwu, configurable)
        ├── agents/                  # Agent definitions
        └── users/                   # User data only
        
        Note: skills/ and channels/ are now external directories configured via
        skills_root and channels_root in xuanwu.json.
        
        Returns:
            True if initialization was successful.
        """
        try:
            # Create workspace directory structure
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            (self.workspace_path / "agents").mkdir(exist_ok=True)
            # skills/ and channels/ are now external (skills_root, channels_root)
            
            # Create users directory inside workspace
            self.users_dir.mkdir(exist_ok=True)
            
            # Create default main agent if not exists
            self._create_default_main_agent()
            
            return True
        except Exception as e:
            print(f"[WorkspaceInitializer] Failed to initialize workspace: {e}")
            return False
    
    def _create_default_main_agent(self) -> None:
        """Ensure the default main agent exists from repo templates."""
        main_agent_dir = self.workspace_path / "agents" / "main"
        main_agent_dir.mkdir(parents=True, exist_ok=True)

        template_dir = self._get_default_main_agent_template_dir()
        for filename in self._main_agent_filenames:
            target_path = main_agent_dir / filename
            if target_path.exists():
                continue
            shutil.copy2(template_dir / filename, target_path)

    def _get_default_main_agent_template_dir(self) -> Path:
        """Return the repo template directory for the default main agent."""
        return Path(__file__).resolve().parent.parent / "templates" / "agents" / "main"

    def is_initialized(self) -> bool:

        """Check if workspace is initialized.
        
        Note: skills/ and channels/ are no longer required as they are external.
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
        self.user_dir = self.workspace_path / "users" / user_id
    
    def initialize(self) -> bool:
        """Initialize user directory structure.
        
        Creates the following structure:
        users/<user-id>/
        ├── user_setting.json       # User-specific configs (renamed from xuanwu.json)
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
