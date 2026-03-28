"""Automatic transcript compaction pipeline.

The pipeline monitors estimated context usage and compacts older transcript
segments when the conversation approaches the configured token budget.

Compaction flow:
1. Optionally trigger a memory-flush reminder before compaction
2. Select the older portion of the transcript for compression
3. Generate a summary with an LLM or fallback summarizer
4. Rebuild the message list from the system prompt, summary, and recent turns

Related pruning concepts:
- soft trim: keep the head and tail while dropping part of the middle
- hard clear: aggressively clear transcript state when limits are exceeded
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any, Callable, Awaitable


@dataclass
class CompactionConfig:
    """Configuration for automatic transcript compaction.

    Attributes:
        reserve_tokens_floor: Token budget reserved as a hard floor.
        soft_threshold_tokens: Buffer before the hard floor that triggers
            pre-emptive memory flushing.
        context_window: Total model context window size.
        memory_flush_enabled: Whether memory flush reminders are enabled.
        keep_recent_turns: Number of recent user/assistant turns to preserve.
        keep_last_assistants: Number of recent assistant messages to preserve.
        soft_trim_enabled: Whether soft-trim behavior is enabled.
        hard_clear_threshold: Character threshold for aggressive clearing.
    """
    reserve_tokens_floor: int = 20000
    soft_threshold_tokens: int = 4000
    context_window: int = 128000
    memory_flush_enabled: bool = True
    keep_recent_turns: int = 3
    keep_last_assistants: int = 3
    soft_trim_enabled: bool = True
    hard_clear_threshold: int = 10000


class CompactionPipeline:
    """Compact older transcript segments when context usage grows too large.

    Example:
        ```python
        pipeline = CompactionPipeline(config, summarizer=llm_summarize)

        if pipeline.should_compact(messages, session):
            new_messages = await pipeline.compact(messages, session)
        ```
    """
    
    def __init__(
        self,
        config: CompactionConfig,
        summarizer: Optional[Callable[[list[dict]], Awaitable[str]]] = None,
    ):
        """Initialize the compaction pipeline.

        Args:
            config: Compaction configuration.
            summarizer: Optional async summary generator for message batches.
        """
        self.config = config
        self._summarizer = summarizer
    
    def estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate token usage for a normalized message list.

        This heuristic uses roughly four characters per token and includes
        textual tool-call payloads.
        """
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # Count text blocks inside multimodal content arrays.
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total_chars += len(part["text"])
            
            # Include serialized tool-call payloads in the estimate.
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                total_chars += len(str(tc))
        
        return total_chars // 4
    
    def _resolve_context_window(self, context_window_override: Optional[int] = None) -> int:
        """Resolve effective context window for the current evaluation."""
        if context_window_override and context_window_override > 0:
            return context_window_override
        return self.config.context_window

    def get_available_tokens(self, context_window_override: Optional[int] = None) -> int:
        """Return the token budget available before memory flushing."""
        context_window = self._resolve_context_window(context_window_override)
        return (
            context_window
            - self.config.reserve_tokens_floor
            - self.config.soft_threshold_tokens
        )
    
    def should_compact(
        self,
        messages: list[dict],
        session: Any = None,
        *,
        context_window_override: Optional[int] = None,
    ) -> bool:
        """Return whether the transcript should be compacted now."""
        estimated = self.estimate_tokens(messages)
        context_window = self._resolve_context_window(context_window_override)
        threshold = context_window - self.config.reserve_tokens_floor
        return estimated > threshold
    
    def should_memory_flush(
        self,
        messages: list[dict],
        session: Any = None,
        *,
        context_window_override: Optional[int] = None,
    ) -> bool:
        """Return whether a memory flush reminder should run before compaction."""
        if not self.config.memory_flush_enabled:
            return False
        
        # Only flush once per compaction cycle when the session tracks the flag.
        if session and hasattr(session, "memory_flushed_this_cycle"):
            if session.memory_flushed_this_cycle:
                return False
        
        estimated = self.estimate_tokens(messages)
        threshold = self.get_available_tokens(context_window_override)
        return estimated > threshold

    def _split_for_compaction(self, messages: list[dict]) -> tuple[Optional[dict], list[dict], list[dict]]:
        """Split messages into system prompt, recent messages, and compressible history."""
        system_prompt = messages[0] if messages and messages[0].get("role") == "system" else None

        keep_count = self.config.keep_recent_turns * 2
        recent_messages = messages[-keep_count:] if keep_count > 0 else []

        start_idx = 1 if system_prompt else 0
        end_idx = len(messages) - keep_count if keep_count > 0 else len(messages)
        to_compress = messages[start_idx:end_idx]
        return system_prompt, recent_messages, to_compress

    async def summarize_overflow(self, messages: list[dict]) -> str:
        """Generate a summary for the overflow section only (without rebuilding transcript)."""
        if len(messages) <= self.config.keep_recent_turns * 2 + 1:
            return ""
        _, _, to_compress = self._split_for_compaction(messages)
        if not to_compress:
            return ""
        return await self._generate_summary(to_compress)
    
    async def compact(
        self,
        messages: list[dict],
        session: Any = None,
    ) -> list[dict]:
        """Compact the transcript and return a rebuilt message list."""
        if len(messages) <= self.config.keep_recent_turns * 2 + 1:
            # Not enough history to compact meaningfully.
            return messages

        # 1. Separate the system prompt from compressible history.
        system_prompt, recent_messages, to_compress = self._split_for_compaction(messages)
        
        if not to_compress:
            return messages
        
        # 2. Generate a summary for the older portion.
        summary = await self._generate_summary(to_compress)

        # 3. Rebuild the transcript from the summary and recent turns.
        result = []
        if system_prompt:
            result.append(system_prompt)
        
        # Insert the generated summary as a synthetic system message.
        result.append({
            "role": "system",
            "content": f"[Compression Summary - Earlier conversation has been summarized]\n{summary}",
        })

        # Preserve the recent conversation verbatim.
        result.extend(recent_messages)
        
        return result
    
    async def _generate_summary(self, messages: list[dict]) -> str:
        """Generate a summary for the provided message batch."""
        if self._summarizer:
            return await self._summarizer(messages)
        
        # Build a simple textual summary when no external summarizer is provided.
        summary_parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                # Cap each preview to a short excerpt.
                preview = content[:100] + "..." if len(content) > 100 else content
                summary_parts.append(f"- [{role}]: {preview}")
        
        return "\n".join(summary_parts) if summary_parts else "(No content)"
    
    def prune_tool_results(
        self,
        messages: list[dict],
        mode: str = "soft",
    ) -> list[dict]:
        """Prune tool results to save context space.
        
        Args:
            messages: Message list
            mode: Pruning mode (soft/hard)
            
        Returns:
            Pruned message list
        """
        result = []
        assistant_count = 0
        
        # Count assistant messages from the end
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                assistant_count += 1
        
        current_assistant = assistant_count
        
        for msg in messages:
            if msg.get("role") == "assistant":
                current_assistant -= 1
            
            # Keep recent messages
            if current_assistant < self.config.keep_last_assistants:
                result.append(msg)
                continue
            
            # Handle tool results
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                
                # Check if content contains images
                if isinstance(content, list):
                    has_image = any(
                        isinstance(p, dict) and p.get("type") == "image"
                        for p in content
                    )
                    if has_image:
                        result.append(msg)
                        continue
                
                # Prune large tool results
                if isinstance(content, str) and len(content) > self.config.hard_clear_threshold:
                    if mode == "hard":
                        # Hard clear: remove content entirely
                        msg = msg.copy()
                        msg["content"] = "[Tool result cleared to save context space]"
                    else:
                        # Soft trim: keep head and tail
                        msg = msg.copy()
                        head = content[:500]
                        tail = content[-200:]
                        original_size = len(content)
                        msg["content"] = f"{head}\n...\n{tail}\n[Original size: {original_size} characters]"
            
            result.append(msg)
        
        return result
    
    async def memory_flush(
        self,
        session: Any,
        flush_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """Execute memory flush.
        
        Triggers a silent agent turn that reminds the model to write persistent memory.
        
        Args:
            session: Session metadata
            flush_callback: Optional flush callback
        """
        if flush_callback:
            await flush_callback()
        
        # Mark session as flushed
        if hasattr(session, "memory_flushed_this_cycle"):
            session.memory_flushed_this_cycle = True
