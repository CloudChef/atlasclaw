"""Streaming agent runner built on top of `PydanticAI.iter()`.

The runner adds checkpoint-style controls around agent execution:
- abort-signal checks
- timeout and context checks
- tool-call safety limits
- steering message injection from the session queue

Supported hooks:
`before_agent_start`, `llm_input`, `llm_output`, `before_tool_call`,
`after_tool_call`, and `agent_end`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.atlasclaw.agent.compaction import CompactionConfig, CompactionPipeline
from app.atlasclaw.agent.context_pruning import ContextPruningSettings
from app.atlasclaw.agent.history_memory import HistoryMemoryCoordinator
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.agent.runner_execution import RunnerExecutionMixin
from app.atlasclaw.agent.runner_tool_evidence import RunnerToolEvidenceMixin
from app.atlasclaw.agent.runner_tool_gate import RunnerToolGateMixin
from app.atlasclaw.agent.runtime_events import RuntimeEventDispatcher
from app.atlasclaw.agent.session_titles import SessionTitleGenerator
from app.atlasclaw.agent.tool_gate import ToolNecessityGate
from app.atlasclaw.hooks.runtime import HookRuntime

if TYPE_CHECKING:
    from app.atlasclaw.agent.agent_pool import AgentInstancePool
    from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
    from app.atlasclaw.core.token_interceptor import TokenHealthInterceptor
    from app.atlasclaw.hooks.system import HookSystem
    from app.atlasclaw.session.manager import SessionManager
    from app.atlasclaw.session.queue import SessionQueue
    from app.atlasclaw.session.router import SessionManagerRouter


class AgentRunner(RunnerExecutionMixin, RunnerToolGateMixin, RunnerToolEvidenceMixin):
    """Execute a streaming PydanticAI agent with runtime safeguards."""

    REASONING_ONLY_ESCALATION_SECONDS = 6.0
    REASONING_ONLY_MAX_RETRIES = 1
    MODEL_FIRST_NODE_TIMEOUT_SECONDS = 8.0
    MODEL_NEXT_NODE_TIMEOUT_SECONDS = 20.0
    TOOL_GATE_MUST_USE_MIN_CONFIDENCE = 0.85
    TOOL_GATE_SHORT_CIRCUIT_MIN_CONFIDENCE = 0.55
    TOOL_GATE_CLASSIFIER_TIMEOUT_SECONDS = 8.0

    def __init__(
        self,
        agent: Any,  # pydantic_ai.Agent
        session_manager: "SessionManager",
        prompt_builder: Optional[PromptBuilder] = None,
        compaction: Optional[CompactionPipeline] = None,
        hook_system: Optional["HookSystem"] = None,
        session_queue: Optional["SessionQueue"] = None,
        session_manager_router: Optional["SessionManagerRouter"] = None,
        hook_runtime: Optional[HookRuntime] = None,
        *,
        agent_id: str = "main",
        token_policy: Optional["DynamicTokenPolicy"] = None,
        agent_pool: Optional["AgentInstancePool"] = None,
        token_interceptor: Optional["TokenHealthInterceptor"] = None,
        agent_factory: Optional[Any] = None,
        tool_gate_model_classifier_enabled: bool = True,
        context_pruning_settings: Optional[ContextPruningSettings] = None,
    ):
        """Initialize the agent runner.

        Args:
            agent: PydanticAI agent instance.
            session_manager: Session manager used for transcript persistence.
            prompt_builder: Runtime system prompt builder.
            compaction: Optional compaction pipeline.
            hook_system: Optional hook dispatcher.
            session_queue: Optional queue used for steering message injection.
        """
        self.agent = agent
        self.sessions = session_manager
        self.prompt_builder = prompt_builder or PromptBuilder(PromptBuilderConfig())
        self.compaction = compaction or CompactionPipeline(CompactionConfig())
        self.hooks = hook_system
        self.queue = session_queue
        self.session_manager_router = session_manager_router
        self.agent_id = agent_id
        self.token_policy = token_policy
        self.agent_pool = agent_pool
        self.token_interceptor = token_interceptor
        self.agent_factory = agent_factory
        self.tool_gate_model_classifier_enabled = tool_gate_model_classifier_enabled
        self.context_pruning_settings = context_pruning_settings or ContextPruningSettings()
        self.history = HistoryMemoryCoordinator(session_manager_router or self.sessions, self.compaction)
        self.runtime_events = RuntimeEventDispatcher(self.hooks, self.queue, hook_runtime)
        self.title_generator = SessionTitleGenerator()
        self.hook_runtime = hook_runtime
        self.tool_gate = ToolNecessityGate()
