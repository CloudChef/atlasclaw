"""Workspace initialization and management.

This module provides workspace directory structure initialization
and management for AtlasClaw.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class WorkspaceInitializer:
    """Initialize and manage workspace directory structure.
    
    The workspace directory (default: ./.atlasclaw, configurable) contains ONLY user
    runtime data and the core configuration file. Code/config directories (providers,
    skills, channels) are stored in sibling directories configured via *_root settings.
    
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
    
    def initialize(self) -> bool:
        """Initialize workspace directory structure.
        
        Creates the following structure:
        <workspace>/                 (default: ./.atlasclaw, configurable)
        ├── agents/                  # Agent definitions
        └── users/                   # User data only
        
        Note: skills/ and channels/ are now external directories configured via
        skills_root and channels_root in atlasclaw.json.
        
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
        """Create default main agent if it doesn't exist."""
        main_agent_dir = self.workspace_path / "agents" / "main"
        if main_agent_dir.exists():
            return
        
        main_agent_dir.mkdir(parents=True, exist_ok=True)
        
        # Create SOUL.md
        soul_md = main_agent_dir / "SOUL.md"
        if not soul_md.exists():
            soul_md.write_text(self._default_soul_md(), encoding="utf-8")
        
        # Create IDENTITY.md
        identity_md = main_agent_dir / "IDENTITY.md"
        if not identity_md.exists():
            identity_md.write_text(self._default_identity_md(), encoding="utf-8")
        
        # Create USER.md
        user_md = main_agent_dir / "USER.md"
        if not user_md.exists():
            user_md.write_text(self._default_user_md(), encoding="utf-8")
        
        # Create MEMORY.md
        memory_md = main_agent_dir / "MEMORY.md"
        if not memory_md.exists():
            memory_md.write_text(self._default_memory_md(), encoding="utf-8")
    
    def _default_soul_md(self) -> str:
        """Default SOUL.md content."""
        return '''---
agent_id: "main"
name: "Enterprise Assistant"
version: "1.0"
---

## System Prompt

You are an intelligent assistant for the enterprise, helping employees with daily work tasks.

## Capabilities

- Answer enterprise-related questions
- Assist with document and data processing
- Provide technical support

## Available Providers

- jira
- confluence

## Available Skills

- query_knowledge
- create_ticket
'''
    
    def _default_identity_md(self) -> str:
        """Default IDENTITY.md content."""
        return '''---
agent_id: "main"
---

# IDENTITY.md - Agent Identity

## Basic Information

- **Display Name**: Assistant
- **Avatar**: 🤖
- **Tone**: Professional, Friendly, Concise

## Interaction Style

- Provide direct answers first
- Offer detailed explanations when needed
- Respond in English
'''
    
    def _default_user_md(self) -> str:
        """Default USER.md content."""
        return '''---
agent_id: "main"
---

# USER.md - User Interaction Mode

## Personalization Settings

- Remember user preferences
- Adjust response depth based on user role

## Proactive Behaviors

- Proactively notify when important information is detected
'''
    
    def _default_memory_md(self) -> str:
        """Default MEMORY.md content."""
        return '''---
agent_id: "main"
---

# MEMORY.md - Memory Strategy

## Long-term Memory

- Auto-extraction: Yes
- Extraction triggers: Conversation end, key decision points

## Context Management

- Maximum rounds: 20
- Compression strategy: Summary + Key decisions retention
'''
    
    def is_initialized(self) -> bool:
        """Check if workspace is initialized.
        
        Note: skills/ and channels/ are no longer required as they are external.
        """
        return (
            self.workspace_path.exists()
            and (self.workspace_path / "agents").exists()
            and self.users_dir.exists()
        )


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
