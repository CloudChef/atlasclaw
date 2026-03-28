# -*- coding: utf-8 -*-
"""Agent information API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.xuanwu.core.config import get_config

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _get_agent_definition_dir() -> Path:
    """Resolve the main agent definition directory from the active workspace config."""
    workspace_path = Path(get_config().workspace.path).expanduser()
    return workspace_path / "agents" / "main"


def _clean_scalar(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


def _normalize_section_key(title: str) -> str:
    return title.strip().lower().replace(" ", "_")


def _first_paragraph(lines: list[str]) -> str:
    paragraph_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if paragraph_lines:
                break
            continue
        if line.startswith("- "):
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(line)
    return " ".join(paragraph_lines).strip()


class AgentInfoResponse(BaseModel):
    """Agent information response."""
    name: str
    description: str
    welcome_message: str
    soul: Dict[str, Any]


@router.get("/info", response_model=AgentInfoResponse)
async def get_agent_info() -> AgentInfoResponse:
    """Get main agent information including welcome message from SOUL.md.
    
    Returns:
        Agent information with welcome message
    """
    try:
        agent_dir = _get_agent_definition_dir()
        soul_path = agent_dir / "SOUL.md"
        identity_path = agent_dir / "IDENTITY.md"
        
        soul_data = _parse_soul_md(soul_path.read_text(encoding="utf-8")) if soul_path.exists() else {}
        identity_data = _parse_identity_md(identity_path.read_text(encoding="utf-8")) if identity_path.exists() else {}
        
        # Build welcome message from SOUL.md
        welcome_parts = []
        
        # Add name
        name = soul_data.get("name", identity_data.get("name", "Xuanwu"))
        welcome_parts.append(f"Hello! I'm {name}.")
        
        # Add description
        description = (
            soul_data.get("description", "")
            or soul_data.get("system_prompt_summary", "")
            or identity_data.get("role_positioning_summary", "")
            or identity_data.get("description", "")
        )
        if description:
            welcome_parts.append(description)
        
        # Add core values if available
        core_values = soul_data.get("core_values", [])
        if core_values:
            welcome_parts.append(f"\nMy core values: {', '.join(core_values)}")
        
        welcome_message = "\n\n".join(welcome_parts)
        
        return AgentInfoResponse(
            name=name,
            description=description,
            welcome_message=welcome_message,
            soul=soul_data
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load agent info: {str(e)}")


def _parse_soul_md(content: str) -> Dict[str, Any]:
    """Parse SOUL.md content.
    
    Args:
        content: SOUL.md file content
        
    Returns:
        Parsed data dictionary
    """
    data: Dict[str, Any] = {}
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    in_frontmatter = False

    for idx, raw_line in enumerate(content.splitlines()):
        line = raw_line.strip()

        if idx == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                data[_normalize_section_key(key)] = _clean_scalar(value)
            continue

        if line.startswith("# "):
            continue
        if line.startswith("## "):
            current_section = _normalize_section_key(line[3:])
            sections.setdefault(current_section, [])
            continue
        if current_section:
            sections.setdefault(current_section, []).append(raw_line.rstrip())

    for section_name, lines in sections.items():
        summary = _first_paragraph(lines)
        if summary:
            data[f"{section_name}_summary"] = summary

    return data


def _parse_identity_md(content: str) -> Dict[str, Any]:
    """Parse IDENTITY.md content.
    
    Args:
        content: IDENTITY.md file content
        
    Returns:
        Parsed data dictionary
    """
    data: Dict[str, Any] = {}
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    in_frontmatter = False

    for idx, raw_line in enumerate(content.splitlines()):
        line = raw_line.strip()

        if idx == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                data[_normalize_section_key(key)] = _clean_scalar(value)
            continue

        if line.startswith("# "):
            continue
        if line.startswith("## "):
            current_section = _normalize_section_key(line[3:])
            sections.setdefault(current_section, [])
            continue
        if current_section:
            sections.setdefault(current_section, []).append(raw_line.rstrip())

    for section_name, lines in sections.items():
        summary = _first_paragraph(lines)
        if summary:
            data[f"{section_name}_summary"] = summary

    return data
