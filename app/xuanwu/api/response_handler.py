# -*- coding: utf-8 -*-
"""



Response-Handler message handling

implement streaming, andreply.
corresponds to tasks.md 7.5.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Optional, Protocol


class BlockStreamingBreak(Enum):
    """Chunk breakpoint mode"""
    TEXT_END = "text_end"  # 
    MESSAGE_END = "message_end"  # message


class HumanDelayMode(Enum):
    """Human-like delay mode"""
    OFF = "off"  # 
    NATURAL = "natural"  # 800-2500ms
    CUSTOM = "custom"  # 


@dataclass
class BlockStreamingConfig:
    """Chunked streaming configuration"""
    enabled: bool = False
    break_mode: BlockStreamingBreak = BlockStreamingBreak.TEXT_END
    min_chars: int = 800
    max_chars: int = 1200
    break_preference: str = "paragraph"  # paragraph / newline / sentence / whitespace
    coalesce_idle_ms: int = 500  # 
    coalesce_max_chars: int = 2000  # character count


@dataclass
class HumanDelayConfig:
    """Human-like delay configuration"""
    mode: HumanDelayMode = HumanDelayMode.OFF
    min_ms: int = 800
    max_ms: int = 2500
    
    def get_delay_seconds(self) -> float:
        """get count"""
        if self.mode == HumanDelayMode.OFF:
            return 0.0
        elif self.mode == HumanDelayMode.NATURAL:
            return random.uniform(0.8, 2.5)
        else:  # CUSTOM
            return random.uniform(self.min_ms / 1000, self.max_ms / 1000)


@dataclass
class ResponseConfig:
    """Response configuration"""
    # streaming
    block_streaming: BlockStreamingConfig = field(default_factory=BlockStreamingConfig)
    
    # 
    human_delay: HumanDelayConfig = field(default_factory=HumanDelayConfig)
    
    # channel
    text_chunk_limit: int = 4096  # channel
    
    # reply
    response_prefix: str = ""
    reply_to_mode: str = "auto"  # auto / always / never
    
    # 
    no_reply_token: str = "NO_REPLY"


@dataclass
class ResponseChunk:
    """Response chunk"""
    content: str
    is_final: bool = False
    chunk_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(Protocol):
    """Channel adapter protocol"""
    
    async def send_message(self, content: str, **kwargs: Any) -> bool:
        """Send a message"""
        ...
    
    async def send_typing_indicator(self) -> bool:
        """Send a typing indicator"""
        ...
    
    async def send_chunk(self, chunk: ResponseChunk) -> bool:
        """"""
        ...


class ResponseHandler:
    """

Message handler
    
    handle, and.
    
    Example usage::
    
        handler = ResponseHandler(config)
        
        # handlestreaming
        async for chunk in handler.process(response_stream):
            await adapter.send_chunk(chunk)
    
"""
    
    def __init__(self, config: Optional[ResponseConfig] = None) -> None:
        """


initializehandle
 
 Args:
 config:Response configuration
 
"""
        self.config = config or ResponseConfig()
        self._buffer = ""
        self._chunk_index = 0
    
    async def process(
        self,
        content_stream: AsyncIterator[str],
        *,
        adapter: Optional[ChannelAdapter] = None,
    ) -> AsyncIterator[ResponseChunk]:
        """Process a streamed response into channel-sized chunks.

        Args:
            content_stream: Incoming content delta stream.
            adapter: Optional channel adapter used for typing indicators.

        Yields:
            Response chunks ready for downstream delivery.
        """
        self._buffer = ""
        self._chunk_index = 0
        last_yield_time = time.monotonic()
        
        # Send a typing indicator
        if adapter:
            await adapter.send_typing_indicator()
        
        async for delta in content_stream:
            # Remove the configured no-reply marker if present.
            if self.config.no_reply_token in delta:
                # Filter the marker from the outgoing text.
                delta = delta.replace(self.config.no_reply_token, "")
                if not delta:
                    continue
            
            self._buffer += delta
            
            # check
            if self.config.block_streaming.enabled:
                chunks = self._split_buffer()
                for chunk_content in chunks:
                    # apply
                    delay = self.config.human_delay.get_delay_seconds()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    
                    yield self._create_chunk(chunk_content, is_final=False)
                    last_yield_time = time.monotonic()
            else:
                # mode:check
                idle_ms = (time.monotonic() - last_yield_time) * 1000
                if idle_ms >= self.config.block_streaming.coalesce_idle_ms:
                    if self._buffer:
                        yield self._create_chunk(self._buffer, is_final=False)
                        self._buffer = ""
                        last_yield_time = time.monotonic()
        
        # content
        if self._buffer:
            # applyprefix
            final_content = self._apply_prefix(self._buffer)
            
            # truncatetochannel
            if len(final_content) > self.config.text_chunk_limit:
                final_content = final_content[:self.config.text_chunk_limit - 3] + "..."
            
            yield self._create_chunk(final_content, is_final=True)
    
    def _split_buffer(self) -> list[str]:
        """

split
        
        Returns:
            list
        
"""
        config = self.config.block_streaming
        chunks = []
        
        while len(self._buffer) >= config.min_chars:
            # 
            break_pos = self._find_break_point(
                self._buffer,
                config.min_chars,
                config.max_chars,
                config.break_preference,
            )
            
            if break_pos > 0:
                chunk = self._buffer[:break_pos]
                self._buffer = self._buffer[break_pos:]
                chunks.append(chunk)
            else:
                break
        
        return chunks
    
    def _find_break_point(
        self,
        text: str,
        min_pos: int,
        max_pos: int,
        preference: str,
    ) -> int:
        """


        
        Args:
            text:
            min_pos:
            max_pos:
            preference:Breakpoint preference
            
        Returns:
            (0 to)
        
"""
        # search
        search_end = min(len(text), max_pos)
        
        if search_end < min_pos:
            return 0
        
        # 
        breakpoints = {
            "paragraph": "\n\n",
            "newline": "\n",
            "sentence": ("。", "！", "？", ".", "!", "?"),
            "whitespace": (" ", "\t"),
        }
        
        # search
        if preference == "paragraph":
            search_order = ["paragraph", "newline", "sentence", "whitespace"]
        elif preference == "newline":
            search_order = ["newline", "paragraph", "sentence", "whitespace"]
        elif preference == "sentence":
            search_order = ["sentence", "newline", "paragraph", "whitespace"]
        else:
            search_order = ["whitespace", "sentence", "newline", "paragraph"]
        
        # at [min_pos, max_pos]
        for bp_type in search_order:
            bp_chars = breakpoints.get(bp_type)
            if not bp_chars:
                continue
            
            if isinstance(bp_chars, str):
                bp_chars = (bp_chars,)
            
            # from max_pos min_pos
            for pos in range(search_end - 1, min_pos - 1, -1):
                for bp in bp_chars:
                    if text[pos:pos + len(bp)] == bp:
                        return pos + len(bp)
        
        # to,
        if search_end >= max_pos:
            return max_pos
        
        return 0
    
    def _apply_prefix(self, content: str) -> str:
        """

applyprefix
        
        Args:
            content:raw content
            
        Returns:
            prefixcontent
        
"""
        if self.config.response_prefix and self._chunk_index == 0:
            return self.config.response_prefix + content
        return content
    
    def _create_chunk(self, content: str, is_final: bool) -> ResponseChunk:
        """

createResponse chunk
        
        Args:
            content:content
            is_final:
            
        Returns:
            Response chunk
        
"""
        chunk = ResponseChunk(
            content=content,
            is_final=is_final,
            chunk_index=self._chunk_index,
        )
        self._chunk_index += 1
        return chunk
    
    def suppress_no_reply(self, content: str) -> tuple[str, bool]:
        """

handle
        
        Args:
            content:raw content
            
        Returns:
            (handle content, reply)
        
"""
        token = self.config.no_reply_token
        
        if token in content:
            # 
            cleaned = content.replace(token, "").strip()
            # such as, reply
            return cleaned, not cleaned
        
        return content, False


class NoopChannelAdapter:
    """

No-op channel adapter
    
    used for.
    
"""
    
    async def send_message(self, content: str, **kwargs: Any) -> bool:
        return True
    
    async def send_typing_indicator(self) -> bool:
        return True
    
    async def send_chunk(self, chunk: ResponseChunk) -> bool:
        return True
