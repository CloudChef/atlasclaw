# -*- coding: utf-8 -*-
"""


SSE(Server-Sent Events) manager

implement SSE streaming, used for Agent run.
corresponds to tasks.md 7.4.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Optional

from sse_starlette.sse import EventSourceResponse


class SSEEventType(Enum):
    """SSE event type"""
    LIFECYCLE = "lifecycle"
    ASSISTANT = "assistant"
    TOOL = "tool"
    COMPACTION = "compaction"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    THINKING = "thinking"


@dataclass
class SSEEvent:
    """

SSE event
    
    Attributes:
        event_type:event type
        data:event payload
        event_id:ID(used for resume-from-breakpoint)
        retry:()
    
"""
    event_type: SSEEventType
    data: dict[str, Any]
    event_id: Optional[str] = None
    retry: Optional[int] = None
    
    def to_sse_format(self) -> dict[str, Any]:
        """SSE for mat"""
        def safe_serialize(obj: Any) -> Any:
            if hasattr(obj, '__dict__'):
                return str(obj)
            return obj
        
        result: dict[str, Any] = {
            "event": self.event_type.value,
            "data": json.dumps(self.data, ensure_ascii=False, default=safe_serialize)
        }
        if self.event_id:
            result["id"] = self.event_id
        if self.retry:
            result["retry"] = self.retry
        return result


@dataclass
class StreamState:
    """

Stream state
    
    , used for resume-from-breakpoint.
    
"""
    run_id: str
    last_event_id: str = ""
    event_count: int = 0
    started_at: float = field(default_factory=time.time)
    events: list[SSEEvent] = field(default_factory=list)
    closed: bool = False
    
    def add_event(self, event: SSEEvent) -> None:
        """"""
        self.event_count += 1
        event.event_id = f"{self.run_id}-{self.event_count}"
        self.last_event_id = event.event_id
        # 100 used for resume-from-breakpoint
        self.events.append(event)
        if len(self.events) > 100:
            self.events = self.events[-100:]


class SSEManager:
    """


SSE manager
 
 management SSE, supportmulti run.
 
 Features:
 1.:
 2.:support Last-Event-ID
 3. heartbeat:heartbeat connection
 4.:
 
 Example usage::
 
 manager = SSEManager()
 
 # create
 run_id = "run-123"
 manager.create_stream(run_id)
 
 #
 manager.push_event(run_id, SSEEvent(
 event_type=SSEEventType.ASSISTANT,
 data={"text":"Hello!"}
 ))
 
 # FastAPI
 @app.get("/api/agent/runs/{run_id}/stream")
 async def stream_events(run_id:str):
 return await manager.create_response(run_id)
 
"""
    
    def __init__(
        self,
        *,
        heartbeat_interval: float = 15.0,
        stream_timeout: float = 3600.0,
        max_events_buffer: int = 100,
    ) -> None:
        """

initialize SSE manager
        
        Args:
            heartbeat_interval:heartbeat()
            stream_timeout:()
            max_events_buffer:count
        
"""
        self._heartbeat_interval = heartbeat_interval
        self._stream_timeout = stream_timeout
        self._max_events_buffer = max_events_buffer
        
        # Stream state
        self._streams: dict[str, StreamState] = {}
        
        # 
        self._subscribers: dict[str, list[asyncio.Queue[Optional[SSEEvent]]]] = {}
        
    def create_stream(self, run_id: str) -> StreamState:
        """

create
        
        Args:
            run_id:run ID
            
        Returns:
            Stream state
        
"""
        if run_id not in self._streams:
            self._streams[run_id] = StreamState(run_id=run_id)
            self._subscribers[run_id] = []
        return self._streams[run_id]
        
    def get_stream(self, run_id: str) -> Optional[StreamState]:
        """getStream state"""
        return self._streams.get(run_id)
        
    def push_event(self, run_id: str, event: SSEEvent) -> int:
        """

to
        
        Args:
            run_id:run ID
            event:SSE event
            
        Returns:
            number of notified subscribers
        
"""
        stream = self._streams.get(run_id)
        if not stream:
            return 0
            
        # to-Stream state
        stream.add_event(event)
        
        # 
        queues = self._subscribers.get(run_id, [])
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
                
        return len(queues)
        
    def close_stream(self, run_id: str) -> None:
        """


        
        Args:
            run_id:run ID
        
"""
        stream = self._streams.get(run_id)
        if stream:
            stream.closed = True

        # 
        queues = self._subscribers.get(run_id, [])
        for queue in queues:
            try:
                queue.put_nowait(None)  # None
            except asyncio.QueueFull:
                pass
                
        # (used for resume-from-breakpoint)
        # 
        
    def remove_stream(self, run_id: str) -> None:
        """


        
        Args:
            run_id:run ID
        
"""
        self._streams.pop(run_id, None)
        self._subscribers.pop(run_id, None)
        
    async def create_response(
        self,
        run_id: str,
        *,
        last_event_id: Optional[str] = None,
    ) -> EventSourceResponse:
        """

create SSE
        
        Args:
            run_id:run ID
            last_event_id:ID
            
        Returns:
            EventSourceResponse
        
"""
        return EventSourceResponse(
            self._event_generator(run_id, last_event_id),
            media_type="text/event-stream"
        )
        
    async def _event_generator(
        self,
        run_id: str,
        last_event_id: Optional[str] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """


        
        Args:
            run_id:run ID
            last_event_id:ID
            
        Yields:
            SSE for mat
        
"""
        stream = self._streams.get(run_id)
        if not stream:
            # at,
            yield SSEEvent(
                event_type=SSEEventType.ERROR,
                data={"message": f"Stream not found: {run_id}"}
            ).to_sse_format()
            return
            
        # create
        queue: asyncio.Queue[Optional[SSEEvent]] = asyncio.Queue(maxsize=100)
        
        if run_id not in self._subscribers:
            self._subscribers[run_id] = []
        self._subscribers[run_id].append(queue)
        was_closed_on_subscribe = stream.closed
        
        try:
            replay_events = self._get_missed_events(stream, last_event_id)
            for event in replay_events:
                yield event.to_sse_format()
                # Maintain streaming effect for thinking delta events during replay
                if (event.event_type == SSEEventType.THINKING
                        and event.data.get("phase") == "delta"):
                    await asyncio.sleep(0.015)

            if was_closed_on_subscribe:
                return

            # 
            start_time = time.time()
            
            while True:
                # check
                if time.time() - start_time > self._stream_timeout:
                    yield SSEEvent(
                        event_type=SSEEventType.LIFECYCLE,
                        data={"phase": "timeout"}
                    ).to_sse_format()
                    break
                    
                try:
                    # etc., heartbeat
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._heartbeat_interval
                    )
                    
                    if event is None:
                        # 
                        yield SSEEvent(
                            event_type=SSEEventType.LIFECYCLE,
                            data={"phase": "end"}
                        ).to_sse_format()
                        break
                        
                    yield event.to_sse_format()
                    
                except asyncio.TimeoutError:
                    # heartbeat
                    yield SSEEvent(
                        event_type=SSEEventType.HEARTBEAT,
                        data={"timestamp": datetime.now(timezone.utc).isoformat()}
                    ).to_sse_format()
                    
        finally:
            # 
            if run_id in self._subscribers:
                try:
                    self._subscribers[run_id].remove(queue)
                except ValueError:
                    pass
                    
    def _get_missed_events(
        self,
        stream: StreamState,
        last_event_id: str
    ) -> list[SSEEvent]:
        """get(used for resume-from-breakpoint)"""
        if not last_event_id:
            return list(stream.events)

        missed = []
        found = False
        
        for event in stream.events:
            if found:
                missed.append(event)
            elif event.event_id == last_event_id:
                found = True
                
        return missed
        
    # ----- -----
    
    def push_lifecycle(
        self,
        run_id: str,
        phase: str,
        **kwargs: Any
    ) -> int:
        """


        
        Args:
            run_id:run ID
            phase:phase(start/end/error)
            **kwargs:additional data
            
        Returns:
            number of notified subscribers
        
"""
        return self.push_event(run_id, SSEEvent(
            event_type=SSEEventType.LIFECYCLE,
            data={"phase": phase, **kwargs}
        ))
        
    def push_assistant(
        self,
        run_id: str,
        text: str,
        *,
        is_delta: bool = True,
    ) -> int:
        """


        
        Args:
            run_id:run ID
            text:content
            is_delta:
            
        Returns:
            number of notified subscribers
        
"""
        return self.push_event(run_id, SSEEvent(
            event_type=SSEEventType.ASSISTANT,
            data={"text": text, "is_delta": is_delta}
        ))
        
    def push_tool(
        self,
        run_id: str,
        tool_name: str,
        phase: str,
        **kwargs: Any
    ) -> int:
        """


tool
 
 Args:
 run_id:run ID
 tool_name:tool name
 phase:phase(start/update/end)
 **kwargs:additional data
 
 Returns:
 number of notified subscribers
 
"""
        return self.push_event(run_id, SSEEvent(
            event_type=SSEEventType.TOOL,
            data={"tool": tool_name, "phase": phase, **kwargs}
        ))
        
    def push_error(
        self,
        run_id: str,
        message: str,
        *,
        code: Optional[str] = None,
    ) -> int:
        """


        
        Args:
            run_id:run ID
            message:message
            code:code
            
        Returns:
            number of notified subscribers
        
"""
        data: dict[str, Any] = {"message": message}
        if code:
            data["code"] = code
        return self.push_event(run_id, SSEEvent(
            event_type=SSEEventType.ERROR,
            data=data
        ))

    def push_thinking(
        self,
        run_id: str,
        phase: str,
        content: str = "",
        metadata: dict = None,
    ) -> int:
        """Push a thinking event.
        
        Args:
            run_id: run ID
            phase: phase (start/delta/end)
            content: thinking content (for delta phase)
            metadata: additional metadata (e.g., elapsed time for end phase)
            
        Returns:
            number of notified subscribers
        """
        data: dict[str, Any] = {"phase": phase}
        # Always include content field for frontend consistency
        data["content"] = content
        if metadata:
            data.update(metadata)
        return self.push_event(run_id, SSEEvent(
            event_type=SSEEventType.THINKING,
            data=data
        ))
        
    def get_active_streams(self) -> list[str]:
        """get ID"""
        return list(self._streams.keys())
        
    def get_subscriber_count(self, run_id: str) -> int:
        """get count"""
        return len(self._subscribers.get(run_id, []))
