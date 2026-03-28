"""


Streaming chunker and event definitions

implement Block-Chunker streaming, token, support:
-(min-Chars):>= min-Chars
-(max-Chars):at max-Chars split
- Breakpoint preference:paragraph > newline > sentence > whitespace >
- code:from at split

StreamEvent event type:
- lifecycle:(phase=start/end/error/aborted)
- assistant:
- tool:tool(phase=start/update/end)
- error:
- compaction:(phase=start/end)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StreamEventType(str, Enum):
    """Streaming event type"""
    LIFECYCLE = "lifecycle"
    ASSISTANT = "assistant"
    TOOL = "tool"
    ERROR = "error"
    COMPACTION = "compaction"
    THINKING = "thinking"    # thinking/reasoning process


@dataclass
class StreamEvent:
    """

Streaming event
    
    Attributes:
        type:event type
        phase:phase(lifecycle/tool/compaction use)
        content:content(assistant use)
        tool:tool name(tool use)
        error:(error use)
        metadata:metadata
    
"""
    type: str  # lifecycle | assistant | tool | error | compaction
    phase: str = ""  # start | end | error | aborted | update
    content: str = ""
    tool: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)
    
    @classmethod
    def lifecycle_start(cls) -> "StreamEvent":
        """create start"""
        return cls(type="lifecycle", phase="start")
    
    @classmethod
    def lifecycle_end(cls) -> "StreamEvent":
        """create"""
        return cls(type="lifecycle", phase="end")
    
    @classmethod
    def lifecycle_aborted(cls) -> "StreamEvent":
        """create in"""
        return cls(type="lifecycle", phase="aborted")
    
    @classmethod
    def assistant_delta(cls, content: str) -> "StreamEvent":
        """create"""
        return cls(type="assistant", content=content)
    
    @classmethod
    def tool_start(cls, tool_name: str) -> "StreamEvent":
        """createtoolstart"""
        return cls(type="tool", phase="start", tool=tool_name)
    
    @classmethod
    def tool_end(cls, tool_name: str, result: str = "") -> "StreamEvent":
        """createtool"""
        return cls(type="tool", phase="end", tool=tool_name, content=result)
    
    @classmethod
    def error_event(cls, error: str) -> "StreamEvent":
        """create"""
        return cls(type="error", error=error)
    
    @classmethod
    def compaction_start(cls) -> "StreamEvent":
        """create start"""
        return cls(type="compaction", phase="start")
    
    @classmethod
    def compaction_end(cls) -> "StreamEvent":
        """create"""
        return cls(type="compaction", phase="end")

    @classmethod
    def thinking_start(cls) -> "StreamEvent":
        """开始思考"""
        return cls(type="thinking", phase="start")

    @classmethod
    def thinking_delta(cls, content: str) -> "StreamEvent":
        """思考内容增量"""
        return cls(type="thinking", phase="delta", content=content)

    @classmethod
    def thinking_end(cls, elapsed: float = 0) -> "StreamEvent":
        """End thinking phase"""
        return cls(type="thinking", phase="end", metadata={"elapsed": elapsed})

    def to_dict(self) -> dict:
        """Convert to a dictionary"""
        result = {"type": self.type}
        if self.phase:
            result["phase"] = self.phase
        if self.content:
            result["content"] = self.content
        if self.tool:
            result["tool"] = self.tool
        if self.error:
            result["error"] = self.error
        if self.metadata:
            result["metadata"] = self.metadata
        return result


class BreakPreference(str, Enum):
    """Breakpoint preference"""
    PARAGRAPH = "paragraph"
    NEWLINE = "newline"
    SENTENCE = "sentence"
    WHITESPACE = "whitespace"


class BlockChunker:
    """


streaming
 
 token, configurationcharacters and-Breakpoint preference.
 
 Example usage:
 ```python
 chunker = BlockChunker(min_chars=800, max_chars=1200)
 
 # input text
 for token in llm_stream:
 chunks = chunker.feed(token)
 for chunk in chunks:
 yield chunk
 
 #
 final = chunker.flush()
 if final:
 yield final
 ```
 
"""
    
    # 
    PARAGRAPH_BREAK = re.compile(r'\n\n+')
    NEWLINE_BREAK = re.compile(r'\n')
    SENTENCE_BREAK = re.compile(r'[。！？.!?]\s*')
    WHITESPACE_BREAK = re.compile(r'\s+')
    
    # code
    CODE_FENCE = re.compile(r'```')
    
    def __init__(
        self,
        min_chars: int = 800,
        max_chars: int = 1200,
        break_preference: BreakPreference = BreakPreference.PARAGRAPH,
        idle_ms: int = 300,
        text_chunk_limit: Optional[int] = None,
    ):
        """


initialize
 
 Args:
 min_chars:character count(to)
 max_chars:character count(at split)
 break_preference:Breakpoint preference
 idle_ms:count
 text_chunk_limit:channel(such as, max_chars)
 
"""
        self.min_chars = min_chars
        self.max_chars = min(max_chars, text_chunk_limit) if text_chunk_limit else max_chars
        self.break_preference = break_preference
        self.idle_ms = idle_ms
        
        self._buffer = ""
        self._in_code_fence = False
        self._fence_count = 0
    
    def feed(self, text: str) -> list[str]:
        """

in put text, return list
        
        Args:
            text:
            
        Returns:
            list
        
"""
        self._buffer += text
        self._update_fence_state(text)
        
        chunks = []
        while len(self._buffer) >= self.min_chars:
            # code:at split
            if self._in_code_fence:
                # check in
                if "```" not in self._buffer[self.min_chars:]:
                    # ,
                    break
            
            # at max_chars
            chunk = self._find_break(self._buffer[:self.max_chars])
            if chunk:
                chunks.append(chunk)
                self._buffer = self._buffer[len(chunk):]
                self._update_fence_state(chunk, remove=True)
            else:
                # to, at max_chars split
                # such as at code,
                if self._in_code_fence:
                    chunk = self._force_split_in_fence()
                else:
                    chunk = self._buffer[:self.max_chars]
                    self._buffer = self._buffer[self.max_chars:]
                chunks.append(chunk)
        
        return chunks
    
    def flush(self) -> Optional[str]:
        """


        
        Returns:
            contentor None
        
"""
        if self._buffer:
            chunk = self._buffer
            self._buffer = ""
            self._fence_count = 0
            self._in_code_fence = False
            return chunk
        return None
    
    def reset(self) -> None:
        """"""
        self._buffer = ""
        self._in_code_fence = False
        self._fence_count = 0
    
    def _update_fence_state(self, text: str, remove: bool = False) -> None:
        """code"""
        fence_matches = self.CODE_FENCE.findall(text)
        count = len(fence_matches)
        
        if remove:
            self._fence_count -= count
        else:
            self._fence_count += count
        
        # count at
        self._in_code_fence = self._fence_count % 2 == 1
    
    def _find_break(self, text: str) -> Optional[str]:
        """


        
        Args:
            text:split
            
        Returns:
            split or None
        
"""
        if len(text) < self.min_chars:
            return None
        
        # 
        break_patterns = [
            (BreakPreference.PARAGRAPH, self.PARAGRAPH_BREAK),
            (BreakPreference.NEWLINE, self.NEWLINE_BREAK),
            (BreakPreference.SENTENCE, self.SENTENCE_BREAK),
            (BreakPreference.WHITESPACE, self.WHITESPACE_BREAK),
        ]
        
        # based on search
        pref_index = next(
            (i for i, (p, _) in enumerate(break_patterns) if p == self.break_preference),
            0
        )
        ordered_patterns = break_patterns[pref_index:] + break_patterns[:pref_index]
        
        for _, pattern in ordered_patterns:
            # at min_chars after, max_chars
            search_text = text[self.min_chars:]
            match = pattern.search(search_text)
            if match:
                break_pos = self.min_chars + match.end()
                return text[:break_pos]
        
        return None
    
    def _force_split_in_fence(self) -> str:
        """

at code split
        
        , at.
        
        Returns:
            split(contains)
        
"""
        chunk = self._buffer[:self.max_chars - 4] + "\n```"
        remaining = self._buffer[self.max_chars - 4:]
        
        # at content
        self._buffer = "```\n" + remaining
        
        return chunk


class NoReplyFilter:
    """

NO_REPLY filter
    
    filter contains NO_REPLY, user.
    
"""
    
    NO_REPLY_TOKEN = "NO_REPLY"
    
    @classmethod
    def should_suppress(cls, text: str) -> bool:
        """

check
        
        Args:
            text:
            
        Returns:
            
        
"""
        return cls.NO_REPLY_TOKEN in text
    
    @classmethod
    def filter(cls, text: str) -> str:
        """

filter NO_REPLY
        
        Args:
            text:raw text
            
        Returns:
            filter
        
"""
        return text.replace(cls.NO_REPLY_TOKEN, "").strip()
