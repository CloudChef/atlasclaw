from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from typing import Any, AsyncIterator, Optional

from app.atlasclaw.agent.context_window_guard import ContextWindowInfo, resolve_context_window_info
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.session.context import SessionKey

class RunnerExecutionRuntimeMixin:
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
        if token is None and provider:
            token = self.token_policy.get_or_select_session_token(
                session_key,
                provider=provider,
                model=None,
            )
        if token is None:
            token = self.token_policy.get_or_select_session_token(
                session_key,
                provider=None,
                model=None,
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
    def _resolve_runtime_context_window_info(
        self,
        selected_token_id: Optional[str],
        deps: SkillDeps,
    ) -> ContextWindowInfo:
        """Resolve context window info with source tags for runtime guard checks."""
        selected_token_window: Optional[int] = None
        if selected_token_id and self.token_policy is not None:
            token = self.token_policy.token_pool.tokens.get(selected_token_id)
            context_window = getattr(token, "context_window", None) if token else None
            if isinstance(context_window, int) and context_window > 0:
                selected_token_window = context_window

        extra = deps.extra if isinstance(deps.extra, dict) else {}
        runtime_override = extra.get("context_window") or extra.get("model_context_window")
        models_config_window = (
            extra.get("models_config_context_window")
            or extra.get("configured_context_window")
            or extra.get("provider_config_context_window")
        )
        default_window = self.compaction.config.context_window

        return resolve_context_window_info(
            selected_token_window=selected_token_window,
            models_config_window=models_config_window if isinstance(models_config_window, int) else None,
            runtime_override_window=runtime_override if isinstance(runtime_override, int) else None,
            default_window=default_window,
        )
    def _resolve_runtime_context_window(
        self,
        selected_token_id: Optional[str],
        deps: SkillDeps,
    ) -> Optional[int]:
        """Backward-compatible helper returning only resolved token count."""
        return self._resolve_runtime_context_window_info(selected_token_id, deps).tokens
    def _resolve_session_manager(self, session_key: str, deps: SkillDeps) -> Any:
        """Resolve the correct per-user session manager for the active session."""
        parsed = SessionKey.from_string(session_key)
        scoped_manager = getattr(deps, "session_manager", None)
        scoped_user_id = getattr(scoped_manager, "user_id", None)
        if scoped_manager is not None and scoped_user_id == parsed.user_id:
            return scoped_manager
        if self.session_manager_router is not None:
            return self.session_manager_router.for_session_key(session_key)
        return self.sessions
    async def _maybe_set_draft_title(
        self,
        *,
        session_manager: Any,
        session_key: str,
        session: Any,
        transcript: list[Any],
        user_message: str,
    ) -> None:
        """Create a draft title for brand-new chat threads."""
        if getattr(session, "title_status", "empty") not in {"", "empty"}:
            return
        if transcript:
            return
        draft_title = self.title_generator.build_draft_title(user_message)
        await session_manager.update_title(
            session_key,
            title=draft_title,
            title_status="draft",
        )
        session.title = draft_title
        session.title_status = "draft"
    async def _maybe_finalize_title(
        self,
        *,
        session_manager: Any,
        session_key: str,
        session: Any,
        final_messages: list[dict],
        user_message: str,
    ) -> None:
        """Promote a draft title to a stable final title after the first assistant reply."""
        if getattr(session, "title_status", "empty") == "final":
            return
        assistant_message = next(
            (
                msg.get("content", "")
                for msg in final_messages
                if msg.get("role") == "assistant" and msg.get("content")
            ),
            "",
        )
        final_title = self.title_generator.build_final_title(
            first_user_message=user_message,
            first_assistant_message=assistant_message,
            existing_title=getattr(session, "title", ""),
        )
        await session_manager.update_title(
            session_key,
            title=final_title,
            title_status="final",
        )
        session.title = final_title
        session.title_status = "final"

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
        override_factory = getattr(agent, "override", None)

        if callable(override_factory) and system_prompt:
            override_cm = nullcontext()
            override_candidates = (
                {"instructions": system_prompt},
                {"system_prompt": system_prompt},
            )
            for override_kwargs in override_candidates:
                try:
                    override_cm = override_factory(**override_kwargs)
                    break
                except TypeError:
                    continue
        else:
            override_cm = nullcontext()

        if hasattr(override_cm, "__aenter__"):
            async with override_cm:
                async with agent.iter(
                    user_message,
                    deps=deps,
                    message_history=message_history,
                ) as agent_run:
                    yield agent_run
            return

        with override_cm:
            async with agent.iter(
                user_message,
                deps=deps,
                message_history=message_history,
            ) as agent_run:

                yield agent_run
    async def _iter_agent_nodes(
        self,
        agent_run: Any,
    ) -> AsyncIterator[Any]:
        next_fn = getattr(agent_run, "next", None)
        next_node = getattr(agent_run, "next_node", None)
        if callable(next_fn) and next_node is not None:
            node = next_node
            while node is not None:
                node_type = type(node).__name__.lower()
                if node_type == "end":
                    return
                following_node = await next_fn(node)
                try:
                    setattr(node, "_atlas_next_node", following_node)
                except Exception:
                    pass
                yield node
                node = following_node
            return

        iterator = agent_run.__aiter__()
        while True:
            try:
                node = await iterator.__anext__()
            except StopAsyncIteration:
                return
            yield node
    def _is_model_request_node(self, node: Any) -> bool:
        """Return whether a node represents a model request boundary."""
        node_type = type(node).__name__.lower()
        return "modelrequest" in node_type or node_type.endswith("requestnode")
    def _is_call_tools_node(self, node: Any) -> bool:
        """Return whether a node represents the tool-dispatch boundary."""
        node_type = type(node).__name__.lower()
        return "calltools" in node_type

