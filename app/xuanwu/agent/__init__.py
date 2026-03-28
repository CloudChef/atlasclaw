"""Agent execution layer for Xuanwu.

The `agent` package groups together the components responsible for prompt
construction, iterative agent execution, response compaction, and stream
chunking.
"""

from app.xuanwu.agent.stream import StreamEvent, BlockChunker
from app.xuanwu.agent.compaction import CompactionPipeline, CompactionConfig

__all__ = [
    "StreamEvent",
    "BlockChunker",
    "CompactionPipeline",
    "CompactionConfig",
]
