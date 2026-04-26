# Copyright 2021  Qianyun, Inc. All rights reserved.


"""
System prompt builder.

This module builds the runtime system prompt for each agent loop. It supports
three modes:

- `full`: include the complete runtime prompt.
- `minimal`: omit optional sections used mainly by the primary agent.
- `none`: emit only the minimal identity line.

In full mode, the prompt is assembled in a stable order so the runtime can
reason about tools, safety constraints, skills, workspace context, optional
reply conventions, and runtime metadata consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.atlasclaw.core.config import get_config
from app.atlasclaw.agent.prompt_context_resolver import PromptContextResolver
from app.atlasclaw.agent import prompt_sections

if TYPE_CHECKING:
    from app.atlasclaw.auth.models import UserInfo


class PromptMode(Enum):
    """System prompt mode"""
    FULL = "full"          # include all sections
    MINIMAL = "minimal"    # Sub-agent mode without optional runtime sections
    NONE = "none"          # only the basic identity line


@dataclass
class SandboxConfig:
    """Sandbox configuration"""
    enabled: bool = False
    mode: str = "off"          # off | agent | session
    workspace_root: str = ""
    elevated_exec: bool = False


@dataclass
class PromptBuilderConfig:
    """PromptBuilder configuration"""
    mode: PromptMode = PromptMode.FULL
    bootstrap_max_chars: int = 20000
    bootstrap_total_max_chars: int = 40000
    workspace_path: str = ""
    user_timezone: Optional[str] = None
    time_format: str = "auto"  # auto | 12 | 24
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    agent_name: str = "AtlasClaw"
    agent_description: str = "Enterprise AI Assistant"
    # Marker file used to detect a newly initialized workspace
    new_workspace_marker: str = ".atlasclaw_new_workspace"
    # Total character budget for the Markdown skill index section
    md_skills_max_index_chars: int = 3000
    # Maximum description length for a single Markdown skill entry
    md_skills_desc_max_chars: int = 200
    # Maximum number of Markdown skill entries in the index section
    md_skills_max_count: int = 20
    # Total character budget for the unified capability index section
    capability_index_max_chars: int = 3000
    # Maximum description length for a single unified capability entry
    capability_index_desc_max_chars: int = 200
    # Maximum number of unified capability entries in the index section
    capability_index_max_count: int = 20
    # Maximum bytes to inline for a selected markdown skill body in stage-two expansion
    md_skills_max_file_bytes: int = 262144


class PromptBuilder:
    """
    System prompt builder.

    Fully overrides every section in `system-prompt.md`.

    Example usage:
        ```python
        config = PromptBuilderConfig(
            mode=PromptMode.FULL,
            workspace_path="/path/to/workspace",
        )
        builder = PromptBuilder(config)
        
        system_prompt = builder.build(
            session=session,
            skills=[{"name":"query_vm", "description":"query virtual machines"}],
            tools=[{"name":"read_file", "description":"read file"}],
        )
        ```
    """

    # Bootstrap files injected into the prompt when available
    BOOTSTRAP_FILES = [
        "AGENTS.md",
        "SOUL.md",
        "TOOLS.md",
        "IDENTITY.md",
        "USER.md",
        "HEARTBEAT.md",
        "BOOTSTRAP.md",
    ]
    
    def __init__(self, config: PromptBuilderConfig):
        """Initialize the prompt builder with the provided configuration."""
        if not config.workspace_path:
            config.workspace_path = str(Path(get_config().workspace.path).expanduser().resolve())
        self.config = config
        self._context_resolver = PromptContextResolver()
        self._runtime_warnings: list[str] = []
    
    def build(
        self,
        session: Optional[object] = None,
        skills: Optional[list[dict]] = None,
        tools: Optional[list[dict]] = None,
        md_skills: Optional[list[dict]] = None,
        capability_index: Optional[list[dict]] = None,
        target_md_skill: Optional[dict] = None,
        tool_policy: Optional[dict] = None,
        user_info: Optional["UserInfo"] = None,
        provider_contexts: Optional[dict[str, dict]] = None,
        provider_auth_diagnostics: Optional[dict[str, dict]] = None,
        context_window_tokens: Optional[int] = None,
        mode_override: Optional[PromptMode] = None,
        transcript_skill_hint: Optional[str] = None,
    ) -> str:
        """
        Build the full system prompt for the current run.

        Args:
            session: Optional session metadata for the active conversation.
            skills: Executable skill metadata available to the agent.
            tools: Tool metadata exposed to the current agent.
            md_skills: Optional Markdown skill snapshot for prompt injection.
            provider_contexts: Optional provider LLM context for skill discovery.

        Returns:
            The assembled system prompt text.
        """
        effective_mode = mode_override or self.config.mode

        if effective_mode == PromptMode.NONE:
            return f"You are {self.config.agent_name}, {self.config.agent_description}."

        self._runtime_warnings.clear()
        
        parts = []
        
        # 1. Core identity
        parts.append(self._build_identity())
        
        # 2. Tooling section
        if tools:
            parts.append(self._build_tooling(tools))
        tool_policy_section = self._build_tool_policy(tool_policy)
        if tool_policy_section:
            parts.append(tool_policy_section)
        provider_auth_section = self._build_provider_auth_diagnostics(provider_auth_diagnostics)
        if provider_auth_section:
            parts.append(provider_auth_section)
        
        # 3. Safety section
        parts.append(self._build_safety())
        
        # 3b. User context (when authenticated)
        if user_info and user_info.user_id not in ("anonymous", ""):
            user_ctx = self._build_user_context(user_info)
            if user_ctx:
                parts.append(user_ctx)
        
        if target_md_skill:
            parts.append(self._build_target_md_skill(target_md_skill))

        if transcript_skill_hint:
            parts.append(self._build_skill_continuation_hint(transcript_skill_hint))

        if effective_mode == PromptMode.FULL:
            # 4. Markdown skill index (HIGHEST PRIORITY - check these first!)
            if capability_index is not None:
                capability_section = self._build_capability_index(capability_index)
                if capability_section:
                    parts.append(capability_section)
            else:
                if md_skills:
                    md_index = self._build_md_skills_index(md_skills, provider_contexts)
                    if md_index:
                        parts.append(md_index)

                # 4b. Executable skills (fallback only if no MD skill matches)
                if skills:
                    parts.append(self._build_skills_listing(skills))

            # 5. Self-update instructions
            parts.append(self._build_self_update())
            
            # 6. Workspace context
            parts.append(self._build_workspace_info())
            
            # 7. Documentation pointers
            parts.append(self._build_documentation())
            
            # 8. Project bootstrap context
            bootstrap = self._build_bootstrap(
                session=session,
                context_window_tokens=context_window_tokens,
            )
            if bootstrap:
                parts.append(bootstrap)
            
            # 9. Reply-tag syntax
            reply_tags = self._build_reply_tags()
            if reply_tags:
                parts.append(reply_tags)
            
            # 12. Heartbeats(heartbeat)
            heartbeats = self._build_heartbeats()
            if heartbeats:
                parts.append(heartbeats)
        
        # 9. Sandbox(, when enabled)
        if self.config.sandbox.enabled:
            parts.append(self._build_sandbox())
        
        # 10. Current Date & Time()
        parts.append(self._build_datetime())
        
        # 13. Runtime(runtime information)
        parts.append(self._build_runtime_info())
        
        return "\n\n".join(p for p in parts if p)

    def consume_warnings(self) -> list[str]:
        """Return and clear prompt-build runtime warnings."""
        warnings = list(self._runtime_warnings)
        self._runtime_warnings.clear()
        return warnings

    def _build_target_md_skill(self, target_md_skill: dict[str, str]) -> str:
        return prompt_sections.build_target_md_skill(target_md_skill)

    def _build_skill_continuation_hint(self, hint_skill: str) -> str:
        return prompt_sections.build_skill_continuation_hint(hint_skill)
    
    def _build_user_context(self, user_info: "UserInfo") -> str:
        return prompt_sections.build_user_context(user_info)

    def _build_identity(self) -> str:
        return prompt_sections.build_identity(self.config)
    
    def _build_tooling(self, tools: list[dict]) -> str:
        return prompt_sections.build_tooling(tools)

    def _build_tool_policy(self, tool_policy: Optional[dict]) -> str:
        return prompt_sections.build_tool_policy(tool_policy)

    def _build_provider_auth_diagnostics(self, diagnostics: Optional[dict[str, dict]]) -> str:
        return prompt_sections.build_provider_auth_diagnostics(diagnostics)
    
    def _build_safety(self) -> str:
        return prompt_sections.build_safety()
    
    def _build_skills_listing(self, skills: list[dict]) -> str:
        return prompt_sections.build_skills_listing(skills)
    
    def _build_md_skills_index(
        self,
        md_skills: list[dict],
        provider_contexts: Optional[dict[str, dict]] = None,
    ) -> str:
        return prompt_sections.build_md_skills_index(self.config, md_skills, provider_contexts)

    def _build_capability_index(self, capability_index: list[dict]) -> str:
        return prompt_sections.build_capability_index(self.config, capability_index)
    
    def _build_self_update(self) -> str:
        return prompt_sections.build_self_update()
    
    def _build_workspace_info(self) -> str:
        return prompt_sections.build_workspace_info(self.config)
    
    def _build_documentation(self) -> str:
        return prompt_sections.build_documentation()
    
    def _build_bootstrap(
        self,
        *,
        session: Optional[object] = None,
        context_window_tokens: Optional[int] = None,
    ) -> str:
        """


inject Bootstrap
 
 BOOTSTRAP.md at workspace inject(.atlasclaw_new_workspace).
 (AGENTS.md, SOUL.md etc.) inject.
 
"""
        workspace = Path(self.config.workspace_path).expanduser()
        sections = ["## Project Context", ""]
        
        # workspace(through)
        marker_file = workspace / self.config.new_workspace_marker
        is_new_workspace = marker_file.exists()
        
        target_files: list[str] = []
        for filename in self.BOOTSTRAP_FILES:
            if filename == "BOOTSTRAP.md" and not is_new_workspace:
                continue
            target_files.append(filename)

        existing_target_files: list[str] = []
        for filename in target_files:
            file_path = workspace / filename
            if not file_path.exists():
                self._record_warning(f"Missing bootstrap file: {filename}")
                continue
            existing_target_files.append(filename)

        session_key = getattr(session, "session_key", None) if session is not None else None
        budget_decision = self._context_resolver.resolve_budgets(
            configured_total_budget=self.config.bootstrap_total_max_chars,
            configured_per_file_budget=self.config.bootstrap_max_chars,
            context_window_tokens=context_window_tokens,
        )
        resolved_files = self._context_resolver.resolve(
            workspace=workspace,
            filenames=existing_target_files,
            session_key=str(session_key or ""),
            total_budget=budget_decision.total_budget,
            per_file_budget=budget_decision.per_file_budget,
        )
        for item in resolved_files:
            content = item.content
            if item.truncated:
                self._record_warning(
                    "Bootstrap context truncated by budget: "
                    f"{item.filename} (per_file={budget_decision.per_file_budget}, total={budget_decision.total_budget})"
                )
                content = (
                    f"{content}\n...[Truncated by prompt budget "
                    f"(per_file={budget_decision.per_file_budget}, total={budget_decision.total_budget}, "
                    f"source={budget_decision.source})]"
                )
            sections.append(f"### {item.filename}\n\n{content}")
        
        # inject BOOTSTRAP.md workspace(run)
        if is_new_workspace and marker_file.exists():
            try:
                marker_file.unlink()
            except Exception:
                pass  # 
        
        return "\n\n".join(sections) if len(sections) > 2 else ""

    def _record_warning(self, message: str) -> None:
        normalized = str(message or "").strip()
        if not normalized:
            return
        if normalized in self._runtime_warnings:
            return
        self._runtime_warnings.append(normalized)
    
    def is_new_workspace(self) -> bool:
        """

check workspace
        
        Returns:
            such as workspace at, return True
        
"""
        workspace = Path(self.config.workspace_path).expanduser()
        marker_file = workspace / self.config.new_workspace_marker
        return marker_file.exists()
    
    def mark_as_new_workspace(self) -> None:
        """


convertworkspace workspace
 
 create, build inject BOOTSTRAP.md.
 
"""
        workspace = Path(self.config.workspace_path).expanduser()
        workspace.mkdir(parents=True, exist_ok=True)
        marker_file = workspace / self.config.new_workspace_marker
        marker_file.touch(exist_ok=True)
    
    def _build_sandbox(self) -> str:
        return prompt_sections.build_sandbox(self.config)
    
    def _build_datetime(self) -> str:
        return prompt_sections.build_datetime(self.config)
    
    def _build_reply_tags(self) -> str:
        return prompt_sections.build_reply_tags()
    
    def _build_heartbeats(self) -> str:
        return prompt_sections.build_heartbeats()
    
    def _build_runtime_info(self) -> str:
        return prompt_sections.build_runtime_info()
    
    def get_context_info(self, detail: bool = False) -> dict:
        """Return prompt context metrics used by the `/context` command."""
        workspace = Path(self.config.workspace_path).expanduser()
        
        files_info = []
        total_size = 0
        
        # workspace
        is_new_workspace = self.is_new_workspace()
        
        for filename in self.BOOTSTRAP_FILES:
            # BOOTSTRAP.md at workspace
            if filename == "BOOTSTRAP.md" and not is_new_workspace:
                files_info.append({
                    "filename": filename,
                    "status": "Skipped (not a new workspace)",
                })
                continue
                
            filepath = workspace / filename
            if filepath.exists():
                try:
                    content = filepath.read_text(encoding="utf-8")
                    raw_size = len(content)
                    injected_size = min(raw_size, self.config.bootstrap_max_chars)
                    truncated = raw_size > self.config.bootstrap_max_chars
                    
                    file_entry = {
                        "filename": filename,
                        "raw_size": raw_size,
                        "injected_size": injected_size,
                        "truncated": truncated,
                    }
                    
                    # mode token estimate(4 characters/token)
                    if detail:
                        file_entry["estimated_tokens"] = injected_size // 4
                        
                    files_info.append(file_entry)
                    total_size += injected_size
                except Exception:
                    files_info.append({
                        "filename": filename,
                        "error": "Read failed",
                    })
            else:
                files_info.append({
                    "filename": filename,
                    "status": "Missing",
                })
        
        result = {
            "mode": self.config.mode.value,
            "workspace": str(workspace),
            "is_new_workspace": is_new_workspace,
            "bootstrap_files": files_info,
            "total_bootstrap_size": total_size,
            "bootstrap_max_chars": self.config.bootstrap_max_chars,
            "bootstrap_total_max_chars": self.config.bootstrap_total_max_chars,
        }
        
        # mode
        if detail:
            result["estimated_bootstrap_tokens"] = total_size // 4
            result["sections"] = self._get_section_breakdown()
            
        return result
    
    def _get_section_breakdown(self) -> list[dict]:
        """

get system prompt
        
        Returns:
            list
        
"""
        sections = []
        
        # estimate
        section_estimates = [
            ("identity", "Identity", len(self._build_identity())),
            ("safety", "Safety", len(self._build_safety())),
            ("self_update", "Self-Update", len(self._build_self_update())),
            ("workspace", "Workspace", len(self._build_workspace_info())),
            ("documentation", "Documentation", len(self._build_documentation())),
            ("datetime", "DateTime", len(self._build_datetime())),
            ("runtime", "Runtime", len(self._build_runtime_info())),
        ]
        
        if self.config.sandbox.enabled:
            section_estimates.append(
                ("sandbox", "Sandbox", len(self._build_sandbox()))
            )
        
        for section_id, section_name, char_count in section_estimates:
            sections.append({
                "id": section_id,
                "name": section_name,
                "char_count": char_count,
                "estimated_tokens": char_count // 4,
            })
            
        return sections
    
    def get_context_detail(
        self,
        skills: Optional[list[dict]] = None,
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """Return detailed prompt context metrics for `/context detail`.

        Includes token overhead estimates for the active skills list and tool
        metadata sections.
        """
        # Start from the base context summary.
        result = self.get_context_info(detail=True)
        
        # Calculate skills-section overhead.
        skills_info = {
            "count": 0,
            "char_count": 0,
            "estimated_tokens": 0,
            "items": [],
        }
        
        if skills:
            skills_listing = self._build_skills_listing(skills)
            skills_info["count"] = len(skills)
            skills_info["char_count"] = len(skills_listing)
            skills_info["estimated_tokens"] = len(skills_listing) // 4
            
            for s in skills:
                skills_info["items"].append({
                    "name": s.get("name", "unknown"),
                    "description_length": len(s.get("description", "")),
                })
        
        result["skills"] = skills_info
        
        # calculate Tools schema overhead
        tools_info = {
            "count": 0,
            "char_count": 0,
            "estimated_tokens": 0,
            "items": [],
        }
        
        if tools:
            tools_listing = self._build_tooling(tools)
            tools_info["count"] = len(tools)
            tools_info["char_count"] = len(tools_listing)
            tools_info["estimated_tokens"] = len(tools_listing) // 4
            
            for t in tools:
                tools_info["items"].append({
                    "name": t.get("name", "unknown"),
                    "description_length": len(t.get("description", "")),
                })
        
        result["tools"] = tools_info
        
        # calculate overhead
        total_chars = (
            result["total_bootstrap_size"]
            + skills_info["char_count"]
            + tools_info["char_count"]
            + sum(s["char_count"] for s in result.get("sections", []))
        )
        
        result["total_system_prompt_chars"] = total_chars
        result["total_estimated_tokens"] = total_chars // 4
        
        return result
