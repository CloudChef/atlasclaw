# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
Workspace initialization and management tests.

Tests for WorkspaceInitializer and UserWorkspaceInitializer.
"""

import json
import pytest
from pathlib import Path

from app.atlasclaw.core.workspace import WorkspaceInitializer, UserWorkspaceInitializer


class TemplateWorkspaceInitializer(WorkspaceInitializer):
    """Workspace initializer with a test-controlled template directory."""

    def __init__(self, workspace_path: str, template_dir: Path):
        super().__init__(workspace_path)
        self.template_dir = template_dir

    def _get_default_main_agent_template_dir(self) -> Path:
        return self.template_dir


def write_main_agent_templates(template_dir: Path, *, soul: str = "template soul") -> None:
    """Write a complete main-agent template set for workspace sync tests."""
    template_dir.mkdir(parents=True, exist_ok=True)
    templates = {
        "SOUL.md": soul,
        "IDENTITY.md": "template identity",
        "USER.md": "template user",
        "MEMORY.md": "template memory",
    }
    for filename, content in templates.items():
        (template_dir / filename).write_text(content, encoding="utf-8")


class TestWorkspaceInitializer:
    """Test WorkspaceInitializer functionality."""

    def test_initialize_creates_directory_structure(self, tmp_path):
        """Test: Creates workspace directory structure
        
        Note: provider packages and standalone skills are external directories;
        channel handlers stay in AtlasClaw core.
        """
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        result = initializer.initialize()
        
        assert result is True
        assert workspace.exists()
        assert (workspace / "agents").exists()
        # Provider packages and standalone skills are external; channels are not created in workspace.
        assert (workspace / "users").exists()

    def test_initialize_creates_default_main_agent(self, tmp_path):
        """Test: Creates default main Agent"""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        initializer.initialize()
        
        main_agent_dir = workspace / "agents" / "main"
        assert main_agent_dir.exists()
        assert (main_agent_dir / "SOUL.md").exists()
        assert (main_agent_dir / "IDENTITY.md").exists()
        assert (main_agent_dir / "USER.md").exists()
        assert (main_agent_dir / "MEMORY.md").exists()

    def test_initialize_idempotent(self, tmp_path):
        """Test: Directory already exists, skip creation"""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        
        # First initialization
        result1 = initializer.initialize()
        assert result1 is True
        
        # Second initialization should also succeed
        result2 = initializer.initialize()
        assert result2 is True
        
        # Directory should still exist
        assert workspace.exists()

    def test_is_initialized_returns_false_for_new_workspace(self, tmp_path):
        """Test: Check uninitialized workspace"""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        assert initializer.is_initialized() is False

    def test_is_initialized_returns_true_for_initialized_workspace(self, tmp_path):
        """Test: Check initialized workspace"""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        initializer.initialize()
        assert initializer.is_initialized() is True

    def test_is_initialized_returns_false_when_main_agent_file_missing(self, tmp_path):
        """Test: Missing main agent files should make workspace incomplete."""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        initializer.initialize()

        (workspace / "agents" / "main" / "SOUL.md").unlink()

        assert initializer.is_initialized() is False

    def test_default_main_agent_content(self, tmp_path):
        """Test: Verify default main Agent file content"""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        initializer.initialize()
        
        soul_md = workspace / "agents" / "main" / "SOUL.md"
        content = soul_md.read_text(encoding="utf-8")
        
        assert "agent_id: \"main\"" in content
        assert "System Prompt" in content
        assert "Capabilities" in content
        assert "AtlasClaw Enterprise AI Assistant" in content

    def test_default_main_agent_matches_repo_template(self, tmp_path):
        """Test: Default main agent files are copied from repo templates."""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))

        initializer.initialize()

        main_agent_dir = workspace / "agents" / "main"
        template_dir = initializer._get_default_main_agent_template_dir()
        for filename in ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"):
            assert (main_agent_dir / filename).read_text(encoding="utf-8") == (
                template_dir / filename
            ).read_text(encoding="utf-8")

    def test_initialize_writes_template_sync_state(self, tmp_path):
        """Test: Initializing default main agent records template sync hashes."""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))

        initializer.initialize()

        state = json.loads((workspace / "runtime_state.json").read_text(encoding="utf-8"))
        assert state["version"] == 1
        for filename in ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"):
            record = state["template_sync"][f"agents/main/{filename}"]
            assert record["template_hash"].startswith("sha256:")
            assert record["target_hash"].startswith("sha256:")

    def test_initialize_overwrites_existing_different_file_without_hash_state(self, tmp_path):
        """Test: Existing installs without hash state are overwritten when content differs."""
        workspace = tmp_path / ".atlasclaw"
        template_dir = tmp_path / "templates" / "agents" / "main"
        write_main_agent_templates(template_dir, soul="template soul v1")

        main_agent_dir = workspace / "agents" / "main"
        main_agent_dir.mkdir(parents=True)
        (main_agent_dir / "SOUL.md").write_text("old local soul", encoding="utf-8")

        initializer = TemplateWorkspaceInitializer(str(workspace), template_dir)
        initializer.initialize()

        assert (main_agent_dir / "SOUL.md").read_text(encoding="utf-8") == "template soul v1"
        state = json.loads((workspace / "runtime_state.json").read_text(encoding="utf-8"))
        assert "agents/main/SOUL.md" in state["template_sync"]

    def test_initialize_overwrites_existing_different_file_with_invalid_hash_state(self, tmp_path):
        """Test: Malformed sync records are treated as missing hash baselines."""
        workspace = tmp_path / ".atlasclaw"
        template_dir = tmp_path / "templates" / "agents" / "main"
        write_main_agent_templates(template_dir, soul="template soul v1")

        main_agent_dir = workspace / "agents" / "main"
        main_agent_dir.mkdir(parents=True)
        (main_agent_dir / "SOUL.md").write_text("old local soul", encoding="utf-8")
        (workspace / "runtime_state.json").write_text(
            json.dumps({
                "version": 1,
                "template_sync": {
                    "agents/main/SOUL.md": {"template_hash": "sha256:old"},
                },
            }),
            encoding="utf-8",
        )

        initializer = TemplateWorkspaceInitializer(str(workspace), template_dir)
        initializer.initialize()

        assert (main_agent_dir / "SOUL.md").read_text(encoding="utf-8") == "template soul v1"

    def test_initialize_records_hash_without_copy_when_target_matches_template(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Test: Matching existing files only update state and do not copy templates."""
        workspace = tmp_path / ".atlasclaw"
        template_dir = tmp_path / "templates" / "agents" / "main"
        write_main_agent_templates(template_dir, soul="same soul")

        main_agent_dir = workspace / "agents" / "main"
        main_agent_dir.mkdir(parents=True)
        for filename in ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"):
            (main_agent_dir / filename).write_text(
                (template_dir / filename).read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        def fail_on_copy(*args, **kwargs):
            raise AssertionError("copy2 should not be called for matching templates")

        monkeypatch.setattr("app.atlasclaw.core.workspace.shutil.copy2", fail_on_copy)

        initializer = TemplateWorkspaceInitializer(str(workspace), template_dir)
        initializer.initialize()

        state = json.loads((workspace / "runtime_state.json").read_text(encoding="utf-8"))
        assert set(state["template_sync"]) == {
            "agents/main/SOUL.md",
            "agents/main/IDENTITY.md",
            "agents/main/USER.md",
            "agents/main/MEMORY.md",
        }

    def test_initialize_updates_unmodified_target_when_template_changes(self, tmp_path):
        """Test: A target unchanged since last sync follows template updates."""
        workspace = tmp_path / ".atlasclaw"
        template_dir = tmp_path / "templates" / "agents" / "main"
        write_main_agent_templates(template_dir, soul="template soul v1")

        initializer = TemplateWorkspaceInitializer(str(workspace), template_dir)
        initializer.initialize()

        write_main_agent_templates(template_dir, soul="template soul v2")
        initializer.initialize()

        soul_md = workspace / "agents" / "main" / "SOUL.md"
        assert soul_md.read_text(encoding="utf-8") == "template soul v2"

    def test_initialize_preserves_user_modified_target_across_repeated_restarts(self, tmp_path):
        """Test: User-modified files are not re-baselined after a skipped sync."""
        workspace = tmp_path / ".atlasclaw"
        template_dir = tmp_path / "templates" / "agents" / "main"
        write_main_agent_templates(template_dir, soul="template soul v1")

        initializer = TemplateWorkspaceInitializer(str(workspace), template_dir)
        initializer.initialize()

        soul_md = workspace / "agents" / "main" / "SOUL.md"
        soul_md.write_text("custom user soul", encoding="utf-8")
        write_main_agent_templates(template_dir, soul="template soul v2")

        initializer.initialize()
        assert soul_md.read_text(encoding="utf-8") == "custom user soul"

        initializer.initialize()
        assert soul_md.read_text(encoding="utf-8") == "custom user soul"

    def test_initialize_restores_only_missing_main_agent_files(self, tmp_path):
        """Test: Existing main agent files are preserved and missing files are restored."""
        workspace = tmp_path / ".atlasclaw"
        initializer = WorkspaceInitializer(str(workspace))
        initializer.initialize()

        main_agent_dir = workspace / "agents" / "main"
        soul_md = main_agent_dir / "SOUL.md"
        user_md = main_agent_dir / "USER.md"

        soul_md.write_text("custom soul", encoding="utf-8")
        user_md.unlink()

        initializer.initialize()

        assert soul_md.read_text(encoding="utf-8") == "custom soul"
        assert user_md.exists()
        assert "User Interaction Configuration" in user_md.read_text(encoding="utf-8")


class TestUserWorkspaceInitializer:
    """Test UserWorkspaceInitializer functionality."""

    def test_initialize_creates_user_directory_structure(self, tmp_path):
        """场景：首次创建用户目录结构（user_id="gang.wu"）
        
        Note: channels/ is no longer created; user-level channel configs
        are stored in user_setting.json instead.
        """
        initializer = UserWorkspaceInitializer(str(tmp_path), "gang.wu")
        result = initializer.initialize()
        
        assert result is True
        user_dir = tmp_path / "users" / "gang.wu"
        assert user_dir.exists()
        # channels/ is no longer created; config is in user_setting.json
        assert (user_dir / "sessions").exists()
        assert (user_dir / "memory").exists()

    def test_initialize_creates_default_user_config(self, tmp_path):
        """场景：创建默认用户级 user_setting.json"""
        initializer = UserWorkspaceInitializer(str(tmp_path), "test_user")
        initializer.initialize()
        
        config_path = tmp_path / "users" / "test_user" / "user_setting.json"
        assert config_path.exists()
        
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        assert "channels" in config
        assert "preferences" in config
        # Note: providers is NOT in user config (system-level only)

    def test_initialize_idempotent(self, tmp_path):
        """场景：用户目录已存在时跳过创建"""
        initializer = UserWorkspaceInitializer(str(tmp_path), "test_user")
        
        # First initialization
        result1 = initializer.initialize()
        assert result1 is True
        
        # Second initialization should also succeed
        result2 = initializer.initialize()
        assert result2 is True

    def test_is_initialized_returns_false_for_new_user(self, tmp_path):
        """场景：检查未初始化用户"""
        initializer = UserWorkspaceInitializer(str(tmp_path), "new_user")
        assert initializer.is_initialized() is False

    def test_is_initialized_returns_true_for_initialized_user(self, tmp_path):
        """场景：检查已初始化用户"""
        initializer = UserWorkspaceInitializer(str(tmp_path), "test_user")
        initializer.initialize()
        assert initializer.is_initialized() is True

    def test_get_sessions_dir(self, tmp_path):
        """场景：获取用户 sessions 目录"""
        initializer = UserWorkspaceInitializer(str(tmp_path), "test_user")
        initializer.initialize()
        
        sessions_dir = initializer.get_sessions_dir()
        assert sessions_dir == tmp_path / "users" / "test_user" / "sessions"
        assert sessions_dir.exists()

    def test_get_memory_dir(self, tmp_path):
        """场景：获取用户 memory 目录"""
        initializer = UserWorkspaceInitializer(str(tmp_path), "test_user")
        initializer.initialize()
        
        memory_dir = initializer.get_memory_dir()
        assert memory_dir == tmp_path / "users" / "test_user" / "memory"
        assert memory_dir.exists()


class TestWorkspaceIntegration:
    """Integration tests for workspace functionality."""

    def test_full_workspace_initialization_flow(self, tmp_path):
        """Test: Service first start, auto-create workspace directory and default main Agent"""
        # Initialize workspace (workspace IS the .atlasclaw directory)
        workspace = tmp_path / ".atlasclaw"
        workspace_init = WorkspaceInitializer(str(workspace))
        workspace_init.initialize()
        
        # Initialize default user (users directory is inside workspace)
        user_init = UserWorkspaceInitializer(str(workspace), "default")
        user_init.initialize()
        
        # Verify complete structure
        assert (workspace / "agents" / "main" / "SOUL.md").exists()
        assert (workspace / "users" / "default" / "sessions").exists()

    def test_workspace_persists_across_restarts(self, tmp_path):
        """Test: Service restart, preserve existing workspace configuration"""
        # First initialization
        workspace = tmp_path / ".atlasclaw"
        workspace_init = WorkspaceInitializer(str(workspace))
        workspace_init.initialize()
        
        # Modify a file
        soul_md = workspace / "agents" / "main" / "SOUL.md"
        original_content = soul_md.read_text(encoding="utf-8")
        soul_md.write_text(original_content + "\n# Modified", encoding="utf-8")
        
        # Second initialization should not overwrite
        workspace_init.initialize()
        
        modified_content = soul_md.read_text(encoding="utf-8")
        assert "# Modified" in modified_content
