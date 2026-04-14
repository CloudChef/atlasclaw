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
        capability_class: Stable routing capability class declared by the tool
            itself, for example `fs_write`, `provider:smartcmp`, or `artifact:pptx`.
        live_data: Whether the tool depends on live external/public data.
        browser_interaction: Whether the tool performs interactive browser actions.
        public_web: Whether the tool is a generic public-web lookup/fetch/browse
            capability rather than a provider/private-system integration.
        requires_approval: Whether the tool requires explicit approval.
        routing_visibility: Whether the tool should be visible in the broad
            capability surface for non-follow-up requests.
        coordination_only: Whether the tool is a supporting coordination helper
            that may remain visible alongside a primary targeted toolset.
    """

    name: str
    description: str = ""
    group: str = ""
    capability_class: str = ""
    live_data: bool = False
    browser_interaction: bool = False
    public_web: bool = False
    requires_approval: bool = False
    routing_visibility: str = "contextual"
    aliases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    use_when: list[str] = field(default_factory=list)
    avoid_when: list[str] = field(default_factory=list)
    result_mode: str = "llm"
    coordination_only: bool = False
