# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Request orchestration pipeline for gateway-driven agent execution.

The orchestrator coordinates the main runtime path:
Gateway/API -> Workflow Engine -> Agent Router -> Skill Registry -> Agent Runner

High-level flow:
1. Receive the request from the gateway or API layer
2. Run intent recognition when workflow routing is enabled
3. Select the target agent from routing rules or intent results
4. Filter skills based on the selected agent configuration
5. Execute the run through `AgentRunner`
6. Return a streaming or aggregated response
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional

from app.atlasclaw.agent.routing import AgentConfig, AgentRouter, DmScope, RoutingContext
from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.auth.models import ANONYMOUS_USER, UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.core.trace import enrich_trace_metadata
from app.atlasclaw.session.context import ChatType, SessionKey, SessionScope
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from app.atlasclaw.core.provider_registry import ServiceProviderRegistry


class IntentType(str, Enum):
    """High-level request intent categories."""

    RESOURCE_QUERY = "resource_query"
    TICKET_SUBMIT = "ticket_submit"
    GENERAL_CHAT = "general_chat"
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    """Structured result returned by intent recognition."""

    intent: IntentType
    confidence: float = 0.0
    agent_id: str = ""
    extracted_entities: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""


class AgentInstance:
    """Runtime wrapper around a configured PydanticAI agent."""

    def __init__(self, config: AgentConfig, pydantic_agent: "Agent", skills: list[SkillMetadata]):
        self.config = config
        self.agent = pydantic_agent
        self.skills = skills
        self.created_at = time.time()

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def model(self) -> str:
        return self.config.model


class AgentFactory:
    """Build and cache runtime agents from agent configuration."""

    def __init__(self, skill_registry: SkillRegistry, default_model: str = "gpt-4o"):
        self.skill_registry = skill_registry
        self.default_model = default_model
        self._agent_cache: dict[str, AgentInstance] = {}

    def create(self, config: AgentConfig) -> AgentInstance:
        if config.id in self._agent_cache:
            return self._agent_cache[config.id]

        allowed_skills = self._filter_skills(config)

        from pydantic_ai import Agent

        model = config.model or self.default_model
        agent = Agent(
            model,
            deps_type=SkillDeps,
            system_prompt=self._build_system_prompt(config),
        )

        for meta, handler in allowed_skills:
            self.skill_registry.register_entry_to_agent(agent, meta, handler)

        instance = AgentInstance(
            config=config,
            pydantic_agent=agent,
            skills=[meta for meta, _ in allowed_skills],
        )
        self._agent_cache[config.id] = instance
        return instance

    def _filter_skills(self, config: AgentConfig) -> list[tuple[SkillMetadata, Any]]:
        allowed: list[tuple[SkillMetadata, Any]] = []
        for name in self.skill_registry.list_skills():
            skill = self.skill_registry.get(name)
            if skill and config.tools.is_allowed(name):
                allowed.append(skill)
        return allowed

    def _build_system_prompt(self, config: AgentConfig) -> str:
        parts = [f"You are {config.id} intelligent assistant."]
        if config.metadata.get("role"):
            parts.append(f"Role: {config.metadata['role']}")
        if config.metadata.get("goal"):
            parts.append(f"Goal: {config.metadata['goal']}")
        return "\n".join(parts)


class IntentRecognizer:
    """Recognize a high-level request intent from user input."""

    INTENT_PROMPT = """Analyze the user input and identify the intent type.

Available intents:
- resource_query: Query cloud resources (VMs, storage, networks, etc.)
- ticket_submit: Submit tickets or service requests
- general_chat: General conversation or Q&A

User input: {user_input}

Return JSON format:
{{"intent": "intent_type", "confidence": 0.0-1.0, "entities": {{}}}}
"""

    def __init__(self, llm_caller: Optional[Callable[[str], str]] = None):
        self._llm_caller = llm_caller

    async def recognize(self, user_input: str) -> IntentResult:
        fast_result = self._fast_match(user_input)
        if fast_result.confidence > 0.8:
            return fast_result

        if self._llm_caller:
            try:
                prompt = self.INTENT_PROMPT.format(user_input=user_input)
                response = self._llm_caller(prompt)
                return self._parse_response(response)
            except Exception:
                pass

        return IntentResult(intent=IntentType.GENERAL_CHAT, confidence=0.5)

    def _fast_match(self, user_input: str) -> IntentResult:
        text = user_input.lower()

        resource_keywords = ["query", "view", "vm", "virtual machine", "resource", "list", "status", "check"]
        if any(kw in text for kw in resource_keywords):
            return IntentResult(
                intent=IntentType.RESOURCE_QUERY,
                confidence=0.85,
                agent_id="resource_agent",
            )

        ticket_keywords = ["request", "ticket", "submit", "create", "expand", "new"]
        if any(kw in text for kw in ticket_keywords):
            return IntentResult(
                intent=IntentType.TICKET_SUBMIT,
                confidence=0.85,
                agent_id="ticket_agent",
            )

        return IntentResult(intent=IntentType.UNKNOWN, confidence=0.3)

    def _parse_response(self, response: str) -> IntentResult:
        import json

        try:
            data = json.loads(response)
            intent_str = data.get("intent", "general_chat")
            intent_map = {
                "resource_query": IntentType.RESOURCE_QUERY,
                "ticket_submit": IntentType.TICKET_SUBMIT,
                "general_chat": IntentType.GENERAL_CHAT,
            }
            return IntentResult(
                intent=intent_map.get(intent_str, IntentType.GENERAL_CHAT),
                confidence=data.get("confidence", 0.7),
                extracted_entities=data.get("entities", {}),
                raw_response=response,
            )
        except Exception:
            return IntentResult(
                intent=IntentType.GENERAL_CHAT,
                confidence=0.5,
                raw_response=response,
            )


def _resolve_chat_type(chat_type: str) -> ChatType:
    value = str(chat_type or "").strip().lower()
    try:
        return ChatType(value)
    except ValueError:
        return ChatType.DM


def _resolve_session_scope(dm_scope: DmScope) -> SessionScope:
    if dm_scope == DmScope.PER_PEER:
        return SessionScope.PER_PEER
    if dm_scope == DmScope.PER_CHANNEL_PEER:
        return SessionScope.PER_CHANNEL_PEER
    if dm_scope == DmScope.PER_ACCOUNT_CHANNEL_PEER:
        return SessionScope.PER_ACCOUNT_CHANNEL_PEER
    return SessionScope.MAIN


class RequestOrchestrator:
    """Coordinate intent recognition, routing, and agent execution."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        session_manager: SessionManager,
        agent_router: Optional[AgentRouter] = None,
        intent_recognizer: Optional[IntentRecognizer] = None,
        agent_factory: Optional[AgentFactory] = None,
        service_provider_registry: Optional["ServiceProviderRegistry"] = None,
    ):
        self.skill_registry = skill_registry
        self.session_manager = session_manager
        self.agent_router = agent_router or AgentRouter()
        self.intent_recognizer = intent_recognizer or IntentRecognizer()
        self.agent_factory = agent_factory or AgentFactory(skill_registry)
        self.service_provider_registry = service_provider_registry
        self._intent_agent_map: dict[IntentType, str] = {
            IntentType.RESOURCE_QUERY: "resource_agent",
            IntentType.TICKET_SUBMIT: "ticket_agent",
            IntentType.GENERAL_CHAT: "main",
        }

    async def process(
        self,
        user_input: str,
        peer_id: str,
        channel: str,
        *,
        user_token: str = "",
        user_info: Optional[UserInfo] = None,
        account_id: str = "",
        guild_id: str = "",
        chat_type: str = "dm",
        extra: Optional[dict] = None,
        max_tool_calls: int = 50,
        timeout_seconds: int = 600,
    ) -> AsyncIterator[StreamEvent]:
        resolved_user_info: UserInfo = (
            user_info
            if user_info is not None
            else (UserInfo(user_id="anonymous", raw_token=user_token) if user_token else ANONYMOUS_USER)
        )
        yield StreamEvent.lifecycle_start()

        try:
            intent_result = await self.intent_recognizer.recognize(user_input)

            agent_config = await self._select_agent(
                intent_result=intent_result,
                peer_id=peer_id,
                channel=channel,
                account_id=account_id,
                guild_id=guild_id,
                chat_type=chat_type,
            )

            agent_instance = self.agent_factory.create(agent_config)

            session_scope = _resolve_session_scope(agent_config.dm_scope)
            session_key = SessionKey(
                agent_id=agent_config.id or "main",
                user_id=resolved_user_info.user_id or "default",
                channel=channel or "main",
                account_id=account_id or "default",
                chat_type=_resolve_chat_type(chat_type),
                peer_id=peer_id or "default",
            ).to_string(
                scope=session_scope,
            )

            deps_extra = extra or {}
            if self.service_provider_registry is not None:
                deps_extra["available_providers"] = self.service_provider_registry.get_available_providers_summary()
                deps_extra["provider_instances"] = self.service_provider_registry.get_all_instance_configs()
                deps_extra["_service_provider_registry"] = self.service_provider_registry

            deps_extra["md_skills_snapshot"] = self.skill_registry.md_snapshot()

            deps = SkillDeps(
                user_info=resolved_user_info,
                peer_id=peer_id,
                session_key=session_key,
                channel=channel,
                extra=enrich_trace_metadata(session_key, extra=deps_extra),
            )

            runner = AgentRunner(
                agent=agent_instance.agent,
                session_manager=self.session_manager,
            )

            async for event in runner.run(
                session_key=session_key,
                user_message=user_input,
                deps=deps,
                max_tool_calls=max_tool_calls,
                timeout_seconds=timeout_seconds,
            ):
                yield event
        except Exception as e:
            yield StreamEvent.error_event(str(e))

        yield StreamEvent.lifecycle_end()

    async def _select_agent(
        self,
        intent_result: IntentResult,
        peer_id: str,
        channel: str,
        account_id: str,
        guild_id: str,
        chat_type: str,
    ) -> AgentConfig:
        if intent_result.confidence > 0.7 and intent_result.agent_id:
            agent = self.agent_router.get_agent(intent_result.agent_id)
            if agent:
                return agent

        if intent_result.intent in self._intent_agent_map:
            agent_id = self._intent_agent_map[intent_result.intent]
            agent = self.agent_router.get_agent(agent_id)
            if agent:
                return agent

        ctx = RoutingContext(
            peer_id=peer_id,
            channel=channel,
            account_id=account_id,
            guild_id=guild_id,
            chat_type=chat_type,
        )
        return self.agent_router.route(ctx)
