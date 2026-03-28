"""Agent definition loading and parsing from Markdown files.

This module provides functionality to load and parse agent definitions
from SOUL.md, IDENTITY.md, USER.md, and MEMORY.md files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any


@dataclass
class AgentConfig:
    """Agent configuration object."""
    agent_id: str = "main"
    name: str = "Assistant"
    version: str = "1.0"
    
    # From SOUL.md
    system_prompt: str = ""
    capabilities: list[str] = field(default_factory=list)
    allowed_providers: list[str] = field(default_factory=list)
    allowed_skills: list[str] = field(default_factory=list)
    
    # From IDENTITY.md
    display_name: str = "Assistant"
    avatar: str = "🤖"
    tone: str = "professional"
    
    # From USER.md
    interaction_style: str = ""
    
    # From MEMORY.md
    memory_strategy: str = ""
    max_context_rounds: int = 20


class AgentDefinitionParser:
    """Parse agent definition from Markdown files."""
    
    @staticmethod
    def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter from markdown content.
        
        Returns:
            Tuple of (frontmatter_dict, body_content)
        """
        frontmatter = {}
        body = content
        
        # Match YAML frontmatter between --- markers
        pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
        match = re.match(pattern, content, re.DOTALL)
        
        if match:
            yaml_content = match.group(1)
            body = match.group(2)
            
            # Simple YAML parsing (key: value pairs)
            for line in yaml_content.strip().split('\n'):
                if ':' in line and not line.strip().startswith('#'):
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip().strip('"\'')
                    frontmatter[key] = value
        
        return frontmatter, body
    
    @classmethod
    def parse_soul_md(cls, content: str) -> dict[str, Any]:
        """Parse SOUL.md content.

        Extracts:
        - system_prompt (from ## System Prompt or ## 系统提示词 section)
        - capabilities (from ## Capabilities or ## 能力范围 section)
        - allowed_providers (from ## Available Providers or ## 可用 Providers section)
        - allowed_skills (from ## Available Skills or ## 可用 Skills section)
        """
        frontmatter, body = cls.parse_frontmatter(content)
        result = dict(frontmatter)

        # Extract system prompt (supports both English and Chinese)
        system_prompt_match = re.search(
            r'##\s*(?:System Prompt|系统提示词)\s*\n(.*?)(?=##|\Z)',
            body,
            re.DOTALL | re.IGNORECASE
        )
        if system_prompt_match:
            result['system_prompt'] = system_prompt_match.group(1).strip()

        # Extract capabilities (supports both English and Chinese)
        capabilities_match = re.search(
            r'##\s*(?:Capabilities|能力范围)\s*\n(.*?)(?=##|\Z)',
            body,
            re.DOTALL | re.IGNORECASE
        )
        if capabilities_match:
            caps_text = capabilities_match.group(1)
            result['capabilities'] = [
                line.strip('- ').strip()
                for line in caps_text.split('\n')
                if line.strip().startswith('-')
            ]

        # Extract allowed providers (supports both English and Chinese)
        providers_match = re.search(
            r'##\s*(?:Available Providers|可用 Providers?)\s*\n(.*?)(?=##|\Z)',
            body,
            re.DOTALL | re.IGNORECASE
        )
        if providers_match:
            providers_text = providers_match.group(1)
            result['allowed_providers'] = [
                line.strip('- ').strip()
                for line in providers_text.split('\n')
                if line.strip().startswith('-')
            ]

        # Extract allowed skills (supports both English and Chinese)
        skills_match = re.search(
            r'##\s*(?:Available Skills|可用 Skills?)\s*\n(.*?)(?=##|\Z)',
            body,
            re.DOTALL | re.IGNORECASE
        )
        if skills_match:
            skills_text = skills_match.group(1)
            result['allowed_skills'] = [
                line.strip('- ').strip()
                for line in skills_text.split('\n')
                if line.strip().startswith('-')
            ]

        return result
    
    @classmethod
    def parse_identity_md(cls, content: str) -> dict[str, Any]:
        """Parse IDENTITY.md content."""
        frontmatter, body = cls.parse_frontmatter(content)
        result = dict(frontmatter)

        # Extract display name (supports both English and Chinese)
        name_match = re.search(
            r'\*\*(?:Display Name|显示名称)\*\*[:：]\s*(.+)',
            body
        )
        if name_match:
            result['display_name'] = name_match.group(1).strip()

        # Extract avatar (supports both English and Chinese)
        avatar_match = re.search(
            r'\*\*(?:Avatar|头像)\*\*[:：]\s*(.+)',
            body
        )
        if avatar_match:
            result['avatar'] = avatar_match.group(1).strip()

        # Extract tone (supports both English and Chinese)
        tone_match = re.search(
            r'\*\*(?:Tone|语气)\*\*[:：]\s*(.+)',
            body
        )
        if tone_match:
            result['tone'] = tone_match.group(1).strip()

        return result
    
    @classmethod
    def parse_user_md(cls, content: str) -> dict[str, Any]:
        """Parse USER.md content."""
        frontmatter, body = cls.parse_frontmatter(content)
        result = dict(frontmatter)

        # Store the interaction style section (supports both English and Chinese)
        interaction_match = re.search(
            r'##\s*(?:Personalization|Personalization Settings|个性化设置)\s*\n(.*?)(?=##|\Z)',
            body,
            re.DOTALL | re.IGNORECASE
        )
        if interaction_match:
            result['interaction_style'] = interaction_match.group(1).strip()

        return result
    
    @classmethod
    def parse_memory_md(cls, content: str) -> dict[str, Any]:
        """Parse MEMORY.md content."""
        frontmatter, body = cls.parse_frontmatter(content)
        result = dict(frontmatter)

        # Extract memory strategy (supports both English and Chinese)
        strategy_match = re.search(
            r'##\s*(?:Context Management|上下文管理)\s*\n(.*?)(?=##|\Z)',
            body,
            re.DOTALL | re.IGNORECASE
        )
        if strategy_match:
            result['memory_strategy'] = strategy_match.group(1).strip()

        # Extract max context rounds (supports both English and Chinese)
        rounds_match = re.search(
            r'(?:Max Turns|最大轮数)[:：]\s*(\d+)',
            body
        )
        if rounds_match:
            result['max_context_rounds'] = int(rounds_match.group(1))

        return result


class AgentLoader:
    """Load agent definitions from workspace directory."""
    
    # Default agent configuration when files are missing
    DEFAULT_CONFIG = AgentConfig(
        agent_id="main",
        name="Assistant",
        version="1.0",
        system_prompt="You are an intelligent assistant for the enterprise, helping employees with daily work tasks.",
        capabilities=["Answer questions", "Provide technical support"],
        allowed_providers=[],
        allowed_skills=[],
        display_name="Assistant",
        avatar="🤖",
        tone="professional",
        interaction_style="",
        memory_strategy="",
        max_context_rounds=20
    )
    
    def __init__(self, workspace_path: str = "."):
        """Initialize agent loader.
        
        Args:
            workspace_path: Path to the workspace root directory.
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.agents_dir = self.workspace_path / "agents"
    
    def load_agent(self, agent_id: str) -> AgentConfig:
        """Load agent configuration from Markdown files.
        
        Args:
            agent_id: Agent identifier.
            
        Returns:
            AgentConfig object.
        """
        agent_dir = self.agents_dir / agent_id
        
        # Start with default config
        config = AgentConfig(
            agent_id=agent_id,
            name=agent_id,
            display_name=agent_id
        )
        
        # Override with values from files
        soul_data = self._load_soul_md(agent_dir)
        identity_data = self._load_identity_md(agent_dir)
        user_data = self._load_user_md(agent_dir)
        memory_data = self._load_memory_md(agent_dir)
        
        # Merge all data
        all_data = {**soul_data, **identity_data, **user_data, **memory_data}
        
        # Update config with loaded data
        for key, value in all_data.items():
            if hasattr(config, key) and value:
                setattr(config, key, value)
        
        # If no system prompt loaded, use default
        if not config.system_prompt:
            config.system_prompt = self.DEFAULT_CONFIG.system_prompt
        
        # If no capabilities loaded, use default
        if not config.capabilities:
            config.capabilities = self.DEFAULT_CONFIG.capabilities
        
        return config
    
    def _load_soul_md(self, agent_dir: Path) -> dict[str, Any]:
        """Load SOUL.md file."""
        soul_path = agent_dir / "SOUL.md"
        if soul_path.exists():
            try:
                content = soul_path.read_text(encoding="utf-8")
                return AgentDefinitionParser.parse_soul_md(content)
            except Exception as e:
                print(f"[AgentLoader] Failed to parse SOUL.md: {e}")
        return {}
    
    def _load_identity_md(self, agent_dir: Path) -> dict[str, Any]:
        """Load IDENTITY.md file."""
        identity_path = agent_dir / "IDENTITY.md"
        if identity_path.exists():
            try:
                content = identity_path.read_text(encoding="utf-8")
                return AgentDefinitionParser.parse_identity_md(content)
            except Exception as e:
                print(f"[AgentLoader] Failed to parse IDENTITY.md: {e}")
        return {}
    
    def _load_user_md(self, agent_dir: Path) -> dict[str, Any]:
        """Load USER.md file."""
        user_path = agent_dir / "USER.md"
        if user_path.exists():
            try:
                content = user_path.read_text(encoding="utf-8")
                return AgentDefinitionParser.parse_user_md(content)
            except Exception as e:
                print(f"[AgentLoader] Failed to parse USER.md: {e}")
        return {}
    
    def _load_memory_md(self, agent_dir: Path) -> dict[str, Any]:
        """Load MEMORY.md file."""
        memory_path = agent_dir / "MEMORY.md"
        if memory_path.exists():
            try:
                content = memory_path.read_text(encoding="utf-8")
                return AgentDefinitionParser.parse_memory_md(content)
            except Exception as e:
                print(f"[AgentLoader] Failed to parse MEMORY.md: {e}")
        return {}
    
    def list_agents(self) -> list[str]:
        """List available agent IDs."""
        if not self.agents_dir.exists():
            return []
        
        return [
            d.name for d in self.agents_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
