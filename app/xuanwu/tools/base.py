"""Shared data structures for built-in tools.

`ToolResult` is the normalized return format for tool execution. It can contain
text or multimodal content, metadata such as exit code or duration, and an
error flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """
    Normalized tool execution result.

    Attributes:
        content: Multimodal content blocks returned by the tool.
        details: Structured metadata such as status, duration, or file paths.
        is_error: Whether the result represents an error.
    """

    content: list[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    is_error: bool = False

    @classmethod
    def text(cls, text: str, details: dict | None = None, is_error: bool = False) -> "ToolResult":
        """Create a text result."""
        return cls(
            content=[{"type": "text", "text": text}],
            details=details or {},
            is_error=is_error,
        )

    @classmethod
    def error(cls, message: str, details: dict | None = None) -> "ToolResult":
        """Create an error result."""
        return cls(
            content=[{"type": "text", "text": message}],
            details=details or {},
            is_error=True,
        )

    @classmethod
    def image(cls, url: str, details: dict | None = None) -> "ToolResult":
        """Create an image result."""
        return cls(
            content=[{"type": "image", "url": url}],
            details=details or {},
        )

    @classmethod
    def multimodal(cls, content: list[dict], details: dict | None = None) -> "ToolResult":
        """Create a multimodal result."""
        return cls(
            content=content,
            details=details or {},
        )

    def to_dict(self) -> dict:
        """Serialize the result to the standard dictionary format."""
        return {
            "content": self.content,
            "details": self.details,
            "is_error": self.is_error,
        }


@dataclass
class ToolMetadata:
    """
    Metadata describing a registered tool.

    Attributes:
        name: Tool name.
        description: Human-readable tool description.
        group: Tool group, such as `fs`, `runtime`, or `web`.
        requires_approval: Whether the tool requires explicit approval.
    """

    name: str
    description: str = ""
    group: str = ""
    requires_approval: bool = False
