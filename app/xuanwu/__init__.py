"""
Xuanwu enterprise assistant framework.

This package provides the Xuanwu runtime built on top of PydanticAI and the
project's supporting infrastructure.

Main modules:
- `core`: Shared configuration, dependency, and provider primitives.
- `agent`: Prompt building, agent execution, compaction, and streaming.
- `workflow`: Workflow orchestration and multi-agent coordination.
- `session`: Session keys, persistence, and queue management.
- `skills`: Executable skills and Markdown-based skill loading.
- `models`: Provider registration, retries, and model failover.
- `hooks`: Lifecycle hook definitions and runtime hook execution.
- `messages`: Command parsing and inbound/outbound message handling.
- `memory`: Long-term memory persistence and search.
- `api`: REST, WebSocket, and SSE integration layers.
"""

__version__ = "0.1.0"
