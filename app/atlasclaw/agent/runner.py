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

import asyncio
import time
import logging
from contextlib import asynccontextmanager, nullcontext
from typing import AsyncIterator, Optional, Any, TYPE_CHECKING


logger = logging.getLogger(__name__)

from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.compaction import CompactionPipeline, CompactionConfig
from app.atlasclaw.agent.history_memory import HistoryMemoryCoordinator
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.agent.runtime_events import RuntimeEventDispatcher
from app.atlasclaw.agent.thinking_stream import ThinkingStreamEmitter

if TYPE_CHECKING:
    from app.atlasclaw.agent.agent_pool import AgentInstancePool
    from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
    from app.atlasclaw.core.token_interceptor import TokenHealthInterceptor


if TYPE_CHECKING:
    from app.atlasclaw.session.manager import SessionManager
    from app.atlasclaw.session.queue import SessionQueue
    from app.atlasclaw.hooks.system import HookSystem



class AgentRunner:
    """Execute a streaming PydanticAI agent with runtime safeguards."""
    
    def __init__(
        self,
        agent: Any,  # pydantic_ai.Agent
        session_manager: "SessionManager",
        prompt_builder: Optional[PromptBuilder] = None,
        compaction: Optional[CompactionPipeline] = None,
        hook_system: Optional["HookSystem"] = None,
        session_queue: Optional["SessionQueue"] = None,
        *,
        agent_id: str = "main",
        token_policy: Optional["DynamicTokenPolicy"] = None,
        agent_pool: Optional["AgentInstancePool"] = None,
        token_interceptor: Optional["TokenHealthInterceptor"] = None,
        agent_factory: Optional[Any] = None,
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
        self.agent_id = agent_id
        self.token_policy = token_policy
        self.agent_pool = agent_pool
        self.token_interceptor = token_interceptor
        self.agent_factory = agent_factory
        self.history = HistoryMemoryCoordinator(self.sessions, self.compaction)
        self.runtime_events = RuntimeEventDispatcher(self.hooks, self.queue)

    
    async def run(
        self,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        *,
        max_tool_calls: int = 50,
        timeout_seconds: int = 600,
    ) -> AsyncIterator[StreamEvent]:
        """Execute one agent turn as a stream of runtime events."""
        start_time = time.monotonic()
        tool_calls_count = 0
        compaction_applied = False
        thinking_emitter = ThinkingStreamEmitter()
        persist_override_messages: Optional[list[dict]] = None
        persist_override_base_len: int = 0
        runtime_agent: Any = self.agent
        selected_token_id: Optional[str] = None
        release_slot: Optional[Any] = None
        flushed_memory_signatures: set[str] = set()


        try:
            yield StreamEvent.lifecycle_start()

            runtime_agent, selected_token_id, release_slot = await self._resolve_runtime_agent(session_key, deps)
            runtime_context_window = self._resolve_runtime_context_window(selected_token_id, deps)

            # --:session + build prompt --

            session = await self.sessions.get_or_create(session_key)
            transcript = await self.sessions.load_transcript(session_key)
            message_history = self.history.build_message_history(transcript)
            message_history = self.history.prune_summary_messages(message_history)

            system_prompt = self._build_system_prompt(session=session, deps=deps, agent=runtime_agent)

            if self.hooks:
                prompt_ctx = await self.hooks.trigger(
                    "before_prompt_build",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                        "system_prompt": system_prompt,
                    },
                )
                system_prompt = prompt_ctx.get("system_prompt", system_prompt)

            # at iter,.
            if self.compaction.should_memory_flush(
                message_history,
                session,
                context_window_override=runtime_context_window,
            ):
                await self.history.flush_history_to_timestamped_memory(
                    session_key=session_key,
                    messages=message_history,
                    deps=deps,
                    session=session,
                    context_window=runtime_context_window,
                    flushed_signatures=flushed_memory_signatures,
                )

            if message_history and self.compaction.should_compact(
                message_history,
                session,
                context_window_override=runtime_context_window,
            ):
                if self.hooks:
                    await self.hooks.trigger(
                        "before_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )
                yield StreamEvent.compaction_start()
                compressed_history = await self.compaction.compact(message_history, session)
                message_history = self.history.normalize_messages(compressed_history)
                message_history = await self.history.inject_memory_recall(message_history, deps)
                await self.sessions.mark_compacted(session_key)
                compaction_applied = True
                yield StreamEvent.compaction_end()
                if self.hooks:
                    await self.hooks.trigger(
                        "after_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )

            # -- hook:before_agent_start --
            if self.hooks:
                start_ctx = await self.hooks.trigger(
                    "before_agent_start",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                    },
                )
                user_message = start_ctx.get("user_message", user_message)

                # llm_input at leastat start trigger
                await self.runtime_events.trigger_llm_input(
                    session_key=session_key,
                    user_message=user_message,
                    system_prompt=system_prompt,
                    message_history=message_history,
                )

            # -- inject user_message to deps, for Skills --
            deps.user_message = user_message

            # ========================================
            # :PydanticAI iter()
            # ========================================
            try:
                model_message_history = self.history.to_model_message_history(message_history)
                async with self._run_iter_with_optional_override(
                    agent=runtime_agent,
                    user_message=user_message,
                    deps=deps,
                    message_history=model_message_history,
                    system_prompt=system_prompt,
                ) as agent_run:

                    print(f"[AgentRunner] Starting agent iteration...")
                    node_count = 0
                    async for node in agent_run:
                        node_count += 1
                        print(f"[AgentRunner] Node {node_count}: {type(node).__name__}")
                        # -- checkpoint 1:abort_signal --
                        if deps.is_aborted():
                            yield StreamEvent.lifecycle_aborted()
                            break

                        # -- checkpoint 2:--
                        if time.monotonic() - start_time > timeout_seconds:
                            yield StreamEvent.error_event("timeout")
                            break

                        # -- checkpoint 3:context -> trigger --
                        current_messages = self.history.normalize_messages(agent_run.all_messages())
                        current_messages = self.history.prune_summary_messages(current_messages)
                        if self.compaction.should_memory_flush(
                            current_messages,
                            session,
                            context_window_override=runtime_context_window,
                        ):
                            await self.history.flush_history_to_timestamped_memory(
                                session_key=session_key,
                                messages=current_messages,
                                deps=deps,
                                session=session,
                                context_window=runtime_context_window,
                                flushed_signatures=flushed_memory_signatures,
                            )

                        if self.compaction.should_compact(
                            current_messages,
                            session,
                            context_window_override=runtime_context_window,
                        ):
                            if self.hooks:
                                await self.hooks.trigger(
                                    "before_compaction",
                                    {
                                        "session_key": session_key,
                                        "message_count": len(current_messages),
                                    },
                                )
                            yield StreamEvent.compaction_start()
                            compressed = await self.compaction.compact(current_messages, session)
                            persist_override_messages = self.history.normalize_messages(compressed)
                            persist_override_messages = await self.history.inject_memory_recall(
                                persist_override_messages,
                                deps,
                            )
                            persist_override_base_len = len(current_messages)
                            await self.sessions.mark_compacted(session_key)
                            compaction_applied = True
                            yield StreamEvent.compaction_end()
                            if self.hooks:
                                await self.hooks.trigger(
                                    "after_compaction",
                                    {
                                        "session_key": session_key,
                                        "message_count": len(persist_override_messages),
                                    },
                                )

                        # -- hook:llm_input() --
                        if self.hooks and self._is_model_request_node(node):
                            await self.runtime_events.trigger_llm_input(
                                session_key=session_key,
                                user_message=user_message,
                                system_prompt=system_prompt,
                                message_history=current_messages,
                            )

                        # Emit model output chunks as assistant deltas.
                        if hasattr(node, "model_response") and node.model_response:
                            async for event in thinking_emitter.emit_from_model_response(
                                model_response=node.model_response,
                                hooks=self.hooks,
                                session_key=session_key,
                            ):
                                yield event
                        elif hasattr(node, "content") and node.content:
                            content = str(node.content)
                            async for event in thinking_emitter.emit_plain_content(
                                content=content,
                                hooks=self.hooks,
                                session_key=session_key,
                            ):
                                yield event

                        # Surface tool activity in the event stream.
                        tool_calls_in_node = self.runtime_events.collect_tool_calls(node)
                        tool_dispatch = await self.runtime_events.dispatch_tool_calls(
                            tool_calls_in_node,
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=max_tool_calls,
                            deps=deps,
                            session_key=session_key,
                        )
                        tool_calls_count = tool_dispatch.tool_calls_count
                        for event in tool_dispatch.events:
                            yield event
                        if tool_dispatch.should_break:
                            break

                    # Ensure thinking phase is properly closed if still active.
                    async for event in thinking_emitter.close_if_active():
                        yield event

                    # Persist the final normalized transcript.
                    final_messages = self.history.normalize_messages(agent_run.all_messages())
                    if persist_override_messages is not None:
                        if len(final_messages) > persist_override_base_len > 0:
                            # Preserve override messages and append new run output.
                            final_messages = persist_override_messages + final_messages[persist_override_base_len:]
                        else:
                            final_messages = persist_override_messages

                    if not thinking_emitter.assistant_emitted:
                        # Try to get response from agent_run.result first (pydantic-ai structure)
                        final_assistant = ""
                        if hasattr(agent_run, "result") and agent_run.result:
                            result = agent_run.result
                            # Try response property first
                            if hasattr(result, "response") and result.response:
                                response = result.response
                                # Extract text content from response parts, excluding thinking parts
                                if hasattr(response, "parts"):
                                    for part in response.parts:
                                        part_kind = getattr(part, "part_kind", "")
                                        # Skip thinking parts, only extract text parts
                                        if part_kind != "thinking" and hasattr(part, "content") and part.content:
                                            content = str(part.content)
                                            if content:
                                                final_assistant = content
                                                break
                                elif hasattr(response, "content") and response.content:
                                    final_assistant = str(response.content)
                            # Try data property as fallback
                            if not final_assistant and hasattr(result, "data") and result.data:
                                final_assistant = str(result.data)
                        
                        # Fallback: search in final_messages
                        if not final_assistant:
                            final_assistant = next(
                                (
                                    msg["content"]
                                    for msg in reversed(final_messages)
                                    if msg.get("role") == "assistant" and msg.get("content")
                                ),
                                "",
                            )
                        
                        if final_assistant:
                            thinking_emitter.assistant_emitted = True
                            yield StreamEvent.assistant_delta(final_assistant)
                    await self.sessions.persist_transcript(session_key, final_messages)

            except Exception as e:
                # Close thinking phase on exception to maintain contract
                async for event in thinking_emitter.close_if_active():
                    yield event
                        
                # Surface agent runtime errors as stream events.
                yield StreamEvent.error_event(f"agent_error: {str(e)}")

            # -- hook:agent_end --
            await self.runtime_events.trigger_agent_end(
                session_key=session_key,
                tool_calls_count=tool_calls_count,
                compaction_applied=compaction_applied,
            )

            yield StreamEvent.lifecycle_end()

        except Exception as e:
            # Close thinking phase on exception to maintain contract
            async for event in thinking_emitter.close_if_active():
                yield event
                
            yield StreamEvent.error_event(str(e))
        finally:
            if selected_token_id and self.token_interceptor is not None:
                headers = self._extract_rate_limit_headers(deps)
                if headers:
                    self.token_interceptor.on_response(selected_token_id, headers)
            if release_slot is not None:
                release_slot()

    async def _resolve_runtime_agent(
        self,
        session_key: str,
        deps: SkillDeps,
    ) -> tuple[Any, Optional[str], Optional[Any]]:
        """Resolve runtime agent instance and optional semaphore release callback."""
        if self.token_policy is None or self.agent_pool is None or self.agent_factory is None:
            return self.agent, None, None

        extra = deps.extra if isinstance(deps.extra, dict) else {}
        provider = extra.get("provider") if isinstance(extra.get("provider"), str) else None
        model = extra.get("model") if isinstance(extra.get("model"), str) else None

        token = self.token_policy.get_or_select_session_token(
            session_key,
            provider=provider,
            model=model,
        )
        if token is None:
            return self.agent, None, None

        instance = await self.agent_pool.get_or_create(
            self.agent_id,
            token,
            self.agent_factory,
        )
        await instance.concurrency_sem.acquire()
        return instance.agent, token.token_id, instance.concurrency_sem.release

    def _extract_rate_limit_headers(self, deps: SkillDeps) -> dict[str, str]:
        """Best-effort extraction of ratelimit headers from deps.extra."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        candidates = [
            extra.get("rate_limit_headers"),
            extra.get("response_headers"),
            extra.get("llm_response_headers"),
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                return {str(k): str(v) for k, v in candidate.items()}
        return {}

    def _resolve_runtime_context_window(
        self,
        selected_token_id: Optional[str],
        deps: SkillDeps,
    ) -> Optional[int]:
        """Resolve context window from current runtime model/token settings."""
        # 1) Current selected token metadata (best source).
        if selected_token_id and self.token_policy is not None:
            token = self.token_policy.token_pool.tokens.get(selected_token_id)
            context_window = getattr(token, "context_window", None) if token else None
            if isinstance(context_window, int) and context_window > 0:
                return context_window

        # 2) Request-scoped overrides from deps.extra.
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        extra_window = extra.get("context_window") or extra.get("model_context_window")
        if isinstance(extra_window, int) and extra_window > 0:
            return extra_window

        # 3) Fall back to configured compaction context window.
        return self.compaction.config.context_window

    @asynccontextmanager

    async def _run_iter_with_optional_override(
        self,
        *,
        agent: Any,
        user_message: str,
        deps: SkillDeps,
        message_history: list[dict],
        system_prompt: str,
    ):

        """Run `agent.iter()` with optional system-prompt overrides."""
        print(f"[AgentRunner] _run_iter_with_optional_override called")
        print(f"[AgentRunner] user_message: {user_message[:100]}...")
        print(f"[AgentRunner] message_history: {len(message_history)} messages")
        
        override_factory = getattr(agent, "override", None)

        if callable(override_factory) and system_prompt:
            try:
                override_cm = override_factory(system_prompt=system_prompt)
                print(f"[AgentRunner] Created override context manager")
            except TypeError:
                override_cm = nullcontext()
                print(f"[AgentRunner] TypeError creating override, using nullcontext")
        else:
            override_cm = nullcontext()
            print(f"[AgentRunner] No override factory or no system_prompt")

        if hasattr(override_cm, "__aenter__"):
            print(f"[AgentRunner] Using async context manager")
            async with override_cm:
                print(f"[AgentRunner] Calling agent.iter()...")
                async with agent.iter(
                    user_message,
                    deps=deps,
                    message_history=message_history,
                ) as agent_run:

                    print(f"[AgentRunner] agent.iter() returned, yielding agent_run")
                    yield agent_run
            return

        print(f"[AgentRunner] Using sync context manager")
        with override_cm:
            async with agent.iter(
                user_message,
                deps=deps,
                message_history=message_history,
            ) as agent_run:

                yield agent_run

    def _build_system_prompt(self, session: Any, deps: SkillDeps, *, agent: Optional[Any] = None) -> str:
        """Build the runtime system prompt for the current session."""
        skills = self._collect_skills_snapshot(deps)
        tools = self._collect_tools_snapshot(agent=agent or self.agent)

        md_skills = self._collect_md_skills_snapshot(deps)
        target_md_skill = self._collect_target_md_skill(deps)
        provider_contexts = self._collect_provider_contexts(deps)
        return self.prompt_builder.build(
            session=session,
            skills=skills,
            tools=tools,
            md_skills=md_skills,
            target_md_skill=target_md_skill,
            user_info=deps.user_info,
            provider_contexts=provider_contexts,
        )

    def _collect_skills_snapshot(self, deps: SkillDeps) -> list[dict]:
        """Read a structured skills snapshot from `deps.extra` if present."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        for key in ("skills_snapshot", "skills"):
            value = extra.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _collect_md_skills_snapshot(self, deps: SkillDeps) -> list[dict]:
        """Read a Markdown-skill snapshot from `deps.extra` if present."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        for key in ("md_skills_snapshot", "md_skills"):
            value = extra.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _collect_target_md_skill(self, deps: SkillDeps) -> Optional[dict]:
        """Read a targeted markdown-skill descriptor from `deps.extra` if present."""
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        value = extra.get("target_md_skill")
        return value if isinstance(value, dict) else None

    def _collect_provider_contexts(self, deps: SkillDeps) -> dict[str, dict]:
        """Collect provider LLM contexts from ServiceProviderRegistry.
        
        Returns:
            Dictionary mapping provider_type to context dict with keys:
            display_name, description, keywords, capabilities, use_when, avoid_when
        """
        extra = deps.extra if isinstance(deps.extra, dict) else {}
        registry = extra.get("_service_provider_registry")
        
        if registry is None:
            return {}
        
        # Check if registry has get_all_provider_contexts method
        get_contexts = getattr(registry, "get_all_provider_contexts", None)
        if get_contexts is None:
            return {}
        
        try:
            contexts = get_contexts()
            # Convert ProviderContext dataclasses to dicts
            result = {}
            for provider_type, ctx in contexts.items():
                if hasattr(ctx, "__dict__"):
                    result[provider_type] = {
                        "display_name": getattr(ctx, "display_name", ""),
                        "description": getattr(ctx, "description", ""),
                        "keywords": getattr(ctx, "keywords", []),
                        "capabilities": getattr(ctx, "capabilities", []),
                        "use_when": getattr(ctx, "use_when", []),
                        "avoid_when": getattr(ctx, "avoid_when", []),
                    }
                elif isinstance(ctx, dict):
                    result[provider_type] = ctx
            return result
        except Exception:
            return {}

    def _collect_tools_snapshot(self, *, agent: Any) -> list[dict]:
        """Collect tool name and description pairs for prompt building."""
        raw_tools = getattr(agent, "tools", None)

        if not raw_tools:
            return []

        tools: list[dict] = []
        for tool in raw_tools:
            if isinstance(tool, dict):
                name = tool.get("name")
                description = tool.get("description", "")
            else:
                name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
                description = getattr(tool, "description", "") or getattr(tool, "__doc__", "") or ""
            if name:
                tools.append({"name": str(name), "description": str(description).strip()})
        return tools

    def _is_model_request_node(self, node: Any) -> bool:
        """Return whether a node represents a model request boundary."""
        node_type = type(node).__name__.lower()
        return "modelrequest" in node_type or node_type.endswith("requestnode")
    
    async def run_single(
        self,
        user_message: str,
        deps: SkillDeps,
        *,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Run a single non-streaming agent call."""
        # Simplified helper that bypasses the streaming session pipeline.
        try:
            result = await self.agent.run(
                user_message,
                deps=deps,
            )
            return result.output if hasattr(result, "output") else str(result)
        except Exception as e:
            return f"[Error: {str(e)}]"


class MockAgentRunner:
    """Testing stub that returns predefined responses and tool calls."""
    
    def __init__(
        self,
        responses: Optional[list[str]] = None,
        tool_calls: Optional[list[dict]] = None,
    ):
        """Initialize the mock runner with scripted outputs."""
        self.responses = responses or ["This is a mock response."]
        self.tool_calls = tool_calls or []
        self._response_index = 0
        self._tool_index = 0
    
    async def run(
        self,
        session_key: str,
        user_message: str,
        deps: SkillDeps,
        **kwargs,
    ) -> AsyncIterator[StreamEvent]:
        """Yield a deterministic mock event stream."""
        yield StreamEvent.lifecycle_start()
        
        # Replay scripted tool calls first.
        for tc in self.tool_calls:
            tool_name = tc.get("name", "mock_tool")
            yield StreamEvent.tool_start(tool_name)
            await asyncio.sleep(0.1)  # 
            yield StreamEvent.tool_end(tool_name, tc.get("result", ""))
        
        # return
        response = self.responses[self._response_index % len(self.responses)]
        self._response_index += 1
        
        # return
        chunk_size = 50
        for i in range(0, len(response), chunk_size):
            chunk = response[i:i + chunk_size]
            yield StreamEvent.assistant_delta(chunk)
            await asyncio.sleep(0.05)  # streaming
        
        yield StreamEvent.lifecycle_end()
