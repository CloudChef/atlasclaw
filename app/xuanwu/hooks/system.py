"""

hook system implement

supportsequential execution()andparallel execution(observe-only) mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable, Optional, Any


class HookPhase(str, Enum):
    """hook phase"""
    # Gateway
    GATEWAY_START = "gateway_start"
    GATEWAY_STOP = "gateway_stop"
    # Session
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    # Agent
    BEFORE_AGENT_START = "before_agent_start"
    AGENT_END = "agent_end"
    # Prompt
    BEFORE_PROMPT_BUILD = "before_prompt_build"
    AGENT_BOOTSTRAP = "agent:bootstrap"
    # LLM
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    # Tool
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    TOOL_RESULT_PERSIST = "tool_result_persist"
    # Message
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENDING = "message_sending"
    MESSAGE_SENT = "message_sent"
    # Compaction
    BEFORE_COMPACTION = "before_compaction"
    AFTER_COMPACTION = "after_compaction"


class HookExecutionMode(str, Enum):
    """hookexecutemode"""
    SEQUENTIAL = "sequential"   # sequential execution, count
    PARALLEL = "parallel"       # parallel execution, observe-only


@dataclass
class HookDefinition:
    """

Hook definition
    
    Attributes:
        phase:hook phase
        handler:handle count(context dict, return context or None)
        priority:(count)
        mode:executemode
        name:hookname(used for and)
    
"""
    phase: HookPhase
    handler: Callable[[dict], Awaitable[Optional[dict]]]
    priority: int = 100
    mode: HookExecutionMode = HookExecutionMode.SEQUENTIAL
    name: str = ""


class HookSystem:
    """

hook system
    
    manageandtrigger hook, support.
    
    Example usage:
        ```python
        hooks = HookSystem()
        
        # registerhook
        async def log_agent_start(ctx:dict) -> dict:
            print(f"Agent starting:{ctx['session_key']}")
            return ctx
        
        hooks.register(HookDefinition(
            phase=HookPhase.BEFORE_AGENT_START,
            handler=log_agent_start,
            name="log_agent_start",
        ))
        
        # triggerhook
        context = await hooks.trigger("before_agent_start", {
            "session_key":"agent:main:dm:user_42",
        })
        ```
    
"""
    
    def __init__(self):
        """initializehook system"""
        self._hooks: dict[HookPhase, list[HookDefinition]] = {
            phase: [] for phase in HookPhase
        }
    
    def register(self, hook: HookDefinition) -> None:
        """
registerhook
        
        Args:
            hook:Hook definition
        
"""
        self._hooks[hook.phase].append(hook)
        # 
        self._hooks[hook.phase].sort(key=lambda h: h.priority)
    
    def unregister(self, phase: HookPhase, name: str) -> bool:
        """

hook
        
        Args:
            phase:hook phase
            name:hookname
            
        Returns:
            
        
"""
        hooks = self._hooks.get(phase, [])
        for i, hook in enumerate(hooks):
            if hook.name == name:
                hooks.pop(i)
                return True
        return False
    
    async def trigger(
        self,
        phase: str | HookPhase,
        context: dict,
    ) -> dict:
        """

triggerhook
        
        Args:
            phase:hook phase(characters or)
            context:context data
            
        Returns:
            handle context data
        
"""
        # characters
        if isinstance(phase, str):
            try:
                phase = HookPhase(phase)
            except ValueError:
                # phase, return
                return context
        
        hooks = self._hooks.get(phase, [])
        if not hooks:
            return context
        
        # sequential executionandparallel executionhook
        sequential = [h for h in hooks if h.mode == HookExecutionMode.SEQUENTIAL]
        parallel = [h for h in hooks if h.mode == HookExecutionMode.PARALLEL]
        
        result = context
        
        # sequential execution(count)
        for hook in sequential:
            try:
                modified = await hook.handler(result)
                if modified is not None:
                    result = modified
            except Exception as e:
                # :in
                print(f"[HookSystem] Hook '{hook.name}' execution failed: {e}")
        
        # parallel execution(observe-only)
        if parallel:
            await asyncio.gather(
                *[self._safe_call(h, result) for h in parallel],
                return_exceptions=True,
            )
        
        return result
    
    async def _safe_call(
        self,
        hook: HookDefinition,
        context: dict,
    ) -> None:
        """hook()"""
        try:
            await hook.handler(context)
        except Exception as e:
            print(f"[HookSystem] Parallel hook '{hook.name}' execution failed: {e}")
    
    def list_hooks(self, phase: Optional[HookPhase] = None) -> list[dict]:
        """

registerhook
        
        Args:
            phase:hook phase(optional,)
            
        Returns:
            hook list
        
"""
        result = []
        
        phases = [phase] if phase else list(HookPhase)
        for p in phases:
            for hook in self._hooks.get(p, []):
                result.append({
                    "phase": p.value,
                    "name": hook.name,
                    "priority": hook.priority,
                    "mode": hook.mode.value,
                })
        
        return result
    
    def clear(self, phase: Optional[HookPhase] = None) -> None:
        """

hook
        
        Args:
            phase:hook phase(optional,)
        
"""
        if phase:
            self._hooks[phase] = []
        else:
            for p in HookPhase:
                self._hooks[p] = []


# hookfactory count

def create_logging_hook(
    phase: HookPhase,
    log_prefix: str = "[Hook]",
) -> HookDefinition:
    """

create hook
    
    Args:
        phase:hook phase
        log_prefix:prefix
        
    Returns:
        Hook definition
    
"""
    async def handler(ctx: dict) -> dict:
        print(f"{log_prefix} {phase.value}: {ctx}")
        return ctx
    
    return HookDefinition(
        phase=phase,
        handler=handler,
        mode=HookExecutionMode.PARALLEL,
        name=f"logging_{phase.value}",
        priority=999,  # 
    )


def create_session_memory_hook() -> HookDefinition:
    """createsession hook(agent_end)"""
    async def handler(ctx: dict) -> dict:
        # at agent_end trigger
        # implement
        return ctx
    
    return HookDefinition(
        phase=HookPhase.AGENT_END,
        handler=handler,
        mode=HookExecutionMode.SEQUENTIAL,
        name="session_memory",
        priority=50,
    )
