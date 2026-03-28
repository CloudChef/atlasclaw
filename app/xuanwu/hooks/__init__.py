"""

hook system

for hook system, support phase:
- Gateway:gateway_start / gateway_stop
- Session:session_start / session_end
- Agent:before_agent_start / agent_end
- Prompt:before_prompt_build / agent:bootstrap
- LLM:llm_input / llm_output
- Tool:before_tool_call / after_tool_call / tool_result_persist
- Message:message_received / message_sending / message_sent
- Compaction:before_compaction / after_compaction
"""

from app.xuanwu.hooks.system import (
    HookPhase,
    HookExecutionMode,
    HookDefinition,
    HookSystem,
)

__all__ = [
    "HookPhase",
    "HookExecutionMode",
    "HookDefinition",
    "HookSystem",
]
