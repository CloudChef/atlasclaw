# -*- coding: utf-8 -*-
"""
聊天场景集成测试。

覆盖：
1. 多轮聊天上下文回注
2. 不同 session_key 会话隔离
3. 群聊历史包装后进入 agent loop
4. 运行中 steer 消息注入
5. PromptBuilder 生成提示词注入到 agent.iter
6. llm_input / llm_output 钩子触发
7. 压缩结果回写并用于后续轮次上下文
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.xuanwu.agent.runner import AgentRunner
from app.xuanwu.core.deps import SkillDeps
from app.xuanwu.hooks import HookDefinition, HookPhase, HookSystem
from app.xuanwu.messages.handler import ChatType, InboundMessage, MessageHandler
from app.xuanwu.session.manager import SessionManager
from app.xuanwu.session.queue import SessionQueue


def _normalize_history_for_assert(messages: list[object]) -> list[dict]:
    normalized: list[dict] = []
    for msg in messages:
        if isinstance(msg, dict):
            normalized.append(
                {
                    "role": str(msg.get("role", "assistant")),
                    "content": str(msg.get("content", "")),
                }
            )
            continue

        kind = str(getattr(msg, "kind", ""))
        parts = getattr(msg, "parts", None) or []

        role = "assistant"
        if kind == "request":
            has_system = any(getattr(part, "part_kind", "") == "system-prompt" for part in parts)
            role = "system" if has_system else "user"
        elif kind == "response":
            role = "assistant"

        chunks: list[str] = []
        for part in parts:
            part_kind = getattr(part, "part_kind", "")
            if part_kind == "thinking":
                continue
            content = getattr(part, "content", "")
            if isinstance(content, str):
                chunks.append(content)
            elif content:
                chunks.append(str(content))

        normalized.append({"role": role, "content": "".join(chunks)})

    return normalized


class _TextNode:
    def __init__(self, content: str):
        self.content = content


class _ToolNode:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name


class _FakeAgentRun:
    def __init__(self, nodes: list[object], all_messages: list[dict]):
        self._nodes = nodes
        self._all_messages = all_messages
        self._index = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._nodes):
            raise StopAsyncIteration
        node = self._nodes[self._index]
        self._index += 1
        return node

    def all_messages(self):
        return self._all_messages


class _ProgressiveAgentRun:
    """根据迭代进度返回不同 all_messages 快照。"""

    def __init__(self, nodes: list[object], snapshots: list[list[dict]]):
        self._nodes = nodes
        self._snapshots = snapshots
        self._index = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._nodes):
            raise StopAsyncIteration
        node = self._nodes[self._index]
        self._index += 1
        return node

    def all_messages(self):
        idx = min(self._index, len(self._snapshots) - 1)
        return self._snapshots[idx]


class _EchoAgent:
    """按用户输入返回固定回复，并记录每次调用上下文。"""

    def __init__(self):
        self.calls: list[dict] = []

    def iter(self, user_message, deps, message_history):
        response = f"回复:{user_message}"
        self.calls.append(
            {
                "user_message": user_message,
                "message_history": _normalize_history_for_assert(list(message_history)),
            }
        )
        final_messages = list(message_history) + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response},
        ]
        return _FakeAgentRun([_TextNode(response)], final_messages)


class _ToolThenReplyAgent:
    """先产生工具节点，再产生文本节点。"""

    def __init__(self):
        self.calls: list[dict] = []

    def iter(self, user_message, deps, message_history):
        self.calls.append(
            {
                "user_message": user_message,
                "message_history": _normalize_history_for_assert(list(message_history)),
            }
        )
        final_messages = list(message_history) + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "已完成处理"},
        ]
        return _FakeAgentRun([_ToolNode("search"), _TextNode("已完成处理")], final_messages)


class _OverrideScope:
    def __init__(self, agent: "_PromptInjectableEchoAgent", system_prompt: str):
        self._agent = agent
        self._system_prompt = system_prompt
        self._previous: str | None = None

    def __enter__(self):
        self._previous = self._agent.active_system_prompt
        self._agent.active_system_prompt = self._system_prompt
        return self

    def __exit__(self, exc_type, exc, tb):
        self._agent.active_system_prompt = self._previous
        return False


class _PromptInjectableEchoAgent(_EchoAgent):
    """支持 override(system_prompt=...) 的 Echo Agent。"""

    def __init__(self):
        super().__init__()
        self.active_system_prompt: str | None = None
        self.override_prompts: list[str] = []

    def override(self, *, system_prompt: str):
        self.override_prompts.append(system_prompt)
        return _OverrideScope(self, system_prompt)

    def iter(self, user_message, deps, message_history):
        response = f"回复:{user_message}"
        self.calls.append(
            {
                "user_message": user_message,
                "message_history": _normalize_history_for_assert(list(message_history)),
                "system_prompt": self.active_system_prompt,
            }
        )
        final_messages = list(message_history) + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response},
        ]
        return _FakeAgentRun([_TextNode(response)], final_messages)


class _RecordingPromptBuilder:
    """记录 build 调用参数，返回固定提示词。"""

    def __init__(self, prompt: str):
        self.prompt = prompt
        self.calls: list[dict] = []

    def build(self, session=None, skills=None, tools=None, md_skills=None, target_md_skill=None, user_info=None, provider_contexts=None):
        self.calls.append(
            {
                "session": session,
                "skills": skills or [],
                "tools": tools or [],
                "md_skills": md_skills or [],
                "target_md_skill": target_md_skill,
                "user_info": user_info,
                "provider_contexts": provider_contexts or {},
            }
        )
        return self.prompt


class _SingleShotCompactionPipeline:
    """仅在首次满足条件时触发压缩，便于验证回写与回注。"""

    def __init__(self):
        self.compact_calls = 0
        self.should_calls = 0
        self.config = SimpleNamespace(context_window=200000)

    def should_memory_flush(self, messages, session=None, context_window_override=None):
        return False

    def should_compact(self, messages, session=None, context_window_override=None):
        self.should_calls += 1
        return self.compact_calls == 0 and self.should_calls == 2 and len(messages) >= 2

    async def compact(self, messages, session=None):
        self.compact_calls += 1
        return [{"role": "system", "content": "[压缩摘要] 保留关键决策"}]


class _InLoopSingleShotCompactionPipeline(_SingleShotCompactionPipeline):
    """仅在本轮 loop 内第一次节点检查时触发压缩。"""

    def should_compact(self, messages, session=None, context_window_override=None):
        self.should_calls += 1
        return self.compact_calls == 0 and self.should_calls == 2


class _GrowingMessagesAgent:
    """all_messages 会随节点推进增长，用于验证 loop 内压缩回写。"""

    def __init__(self):
        self.calls: list[dict] = []

    def iter(self, user_message, deps, message_history):
        self.calls.append(
            {
                "user_message": user_message,
                "message_history": _normalize_history_for_assert(list(message_history)),
            }
        )
        snapshots = [
            list(message_history),
            list(message_history) + [{"role": "user", "content": user_message}],
            list(message_history)
            + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": "已完成处理"},
            ],
        ]
        nodes = [_ToolNode("search"), _TextNode("已完成处理")]
        return _ProgressiveAgentRun(nodes, snapshots)


class _StructuredFinalMessageAgent:
    """Emit no node text and return structured PydanticAI messages at the end."""

    def iter(self, user_message, deps, message_history):
        final_messages = [
            ModelRequest(parts=[UserPromptPart(user_message)]),
            ModelResponse(parts=[TextPart(f"reply:{user_message}")]),
        ]
        return _FakeAgentRun([], final_messages)


async def _collect_events(runner: AgentRunner, session_key: str, user_message: str, deps: SkillDeps):
    events = []
    async for event in runner.run(session_key, user_message, deps):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_chat_multi_turn_persists_and_reuses_context(tmp_path):
    """用户连续两轮聊天：第二轮必须带上第一轮上下文。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    agent = _EchoAgent()
    runner = AgentRunner(agent=agent, session_manager=manager)

    session_key = "agent:main:telegram:dm:user-100"

    first_deps = SkillDeps(user_token="token-1", peer_id="user-100", session_key=session_key)
    first_events = await _collect_events(runner, session_key, "你好", first_deps)

    assert first_events[0].type == "lifecycle" and first_events[0].phase == "start"
    assert first_events[-1].type == "lifecycle" and first_events[-1].phase == "end"
    assert any(e.type == "assistant" and "回复:你好" in e.content for e in first_events)

    second_deps = SkillDeps(user_token="token-1", peer_id="user-100", session_key=session_key)
    await _collect_events(runner, session_key, "继续", second_deps)

    assert len(agent.calls) == 2
    second_history = agent.calls[1]["message_history"]
    assert any(m["role"] == "user" and m["content"] == "你好" for m in second_history)
    assert any(m["role"] == "assistant" and m["content"] == "回复:你好" for m in second_history)

    transcript = await manager.load_transcript(session_key)
    assert len(transcript) == 4
    assert transcript[0].role == "user"
    assert transcript[1].role == "assistant"


@pytest.mark.asyncio
async def test_chat_context_isolated_between_sessions(tmp_path):
    """不同 session_key 的会话上下文不应串话。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    agent = _EchoAgent()
    runner = AgentRunner(agent=agent, session_manager=manager)

    session_a = "agent:main:telegram:dm:user-A"
    session_b = "agent:main:telegram:dm:user-B"

    await _collect_events(runner, session_a, "A1", SkillDeps(peer_id="user-A", session_key=session_a))
    await _collect_events(runner, session_b, "B1", SkillDeps(peer_id="user-B", session_key=session_b))
    await _collect_events(runner, session_a, "A2", SkillDeps(peer_id="user-A", session_key=session_a))

    history_a_second_round = agent.calls[2]["message_history"]
    contents = [m["content"] for m in history_a_second_round]
    assert "A1" in contents
    assert "B1" not in contents

    transcript_a = await manager.load_transcript(session_a)
    transcript_b = await manager.load_transcript(session_b)
    assert len(transcript_a) == 4
    assert len(transcript_b) == 2


@pytest.mark.asyncio
async def test_group_chat_history_wrapped_then_enters_agent_loop(tmp_path):
    """群聊中历史消息包装后，作为当前输入进入 agent loop。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    agent = _EchoAgent()
    runner = AgentRunner(agent=agent, session_manager=manager)

    handler = MessageHandler(debounce_ms=10, group_history_limit=5)
    session_key = "agent:main:telegram:group:team-1"

    handler.add_to_group_history(
        InboundMessage(
            message_id="h1",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-bob",
            chat_type=ChatType.GROUP,
            sender_name="Bob",
            body="昨天发布了新版本",
        ),
        session_key,
    )
    handler.add_to_group_history(
        InboundMessage(
            message_id="h2",
            channel="telegram",
            account_id="acc-1",
            peer_id="user-carol",
            chat_type=ChatType.GROUP,
            sender_name="Carol",
            body="请关注告警波动",
        ),
        session_key,
    )

    current = InboundMessage(
        message_id="m-current",
        channel="telegram",
        account_id="acc-1",
        peer_id="user-alice",
        chat_type=ChatType.GROUP,
        sender_name="Alice",
        body="现在系统状态如何？",
    )

    processed = await handler.process_inbound(current, session_key=session_key, bypass_debounce=True)
    assert processed is not None

    await _collect_events(
        runner,
        session_key,
        processed.body,
        SkillDeps(peer_id="team-1", session_key=session_key),
    )

    injected = agent.calls[0]["user_message"]
    assert injected.startswith("[Alice]")
    assert "[Chat messages since your last reply - for context]" in injected
    assert "[Bob] 昨天发布了新版本" in injected
    assert "[Carol] 请关注告警波动" in injected
    assert "[Current message - respond to this]" in injected


@pytest.mark.asyncio
async def test_chat_steer_messages_injected_during_tool_loop(tmp_path):
    """用户在运行中追加消息，steer 队列内容应注入当前 loop。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    queue = SessionQueue()
    agent = _ToolThenReplyAgent()
    runner = AgentRunner(agent=agent, session_manager=manager, session_queue=queue)

    session_key = "agent:main:telegram:dm:user-300"
    queue.enqueue(session_key, "先看最近告警")
    queue.enqueue(session_key, "优先定位数据库连接问题")

    events = await _collect_events(
        runner,
        session_key,
        "帮我排查线上问题",
        SkillDeps(peer_id="user-300", session_key=session_key),
    )

    tool_events = [e for e in events if e.type == "tool"]
    assert [e.phase for e in tool_events] == ["start", "end"]

    steer_injected = [e.content for e in events if e.type == "assistant" and "用户补充" in e.content]
    assert len(steer_injected) == 1
    assert "先看最近告警" in steer_injected[0]
    assert "优先定位数据库连接问题" in steer_injected[0]
    assert queue.queue_size(session_key) == 0


@pytest.mark.asyncio
async def test_chat_prompt_builder_prompt_injected_into_agent_iter(tmp_path):
    """PromptBuilder 构建结果应通过 agent.override 注入当前 loop。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    agent = _PromptInjectableEchoAgent()
    prompt_builder = _RecordingPromptBuilder("SYSTEM_PROMPT::chat-loop")
    runner = AgentRunner(agent=agent, session_manager=manager, prompt_builder=prompt_builder)

    session_key = "agent:main:telegram:dm:user-400"
    deps = SkillDeps(
        peer_id="user-400",
        session_key=session_key,
        extra={"skills_snapshot": [{"name": "query_alerts", "description": "查询告警"}]},
    )

    await _collect_events(runner, session_key, "检查提示词注入", deps)

    assert len(prompt_builder.calls) == 1
    assert agent.override_prompts == ["SYSTEM_PROMPT::chat-loop"]
    assert agent.calls[0]["system_prompt"] == "SYSTEM_PROMPT::chat-loop"


@pytest.mark.asyncio
async def test_chat_llm_input_and_output_hooks_are_triggered(tmp_path):
    """完整 chat loop 中必须触发 llm_input 与 llm_output 钩子。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    hooks = HookSystem()
    llm_inputs: list[dict] = []
    llm_outputs: list[dict] = []

    async def _capture_input(ctx: dict):
        llm_inputs.append(dict(ctx))
        return ctx

    async def _capture_output(ctx: dict):
        llm_outputs.append(dict(ctx))
        return ctx

    hooks.register(
        HookDefinition(
            phase=HookPhase.LLM_INPUT,
            handler=_capture_input,
            name="capture_llm_input",
        )
    )
    hooks.register(
        HookDefinition(
            phase=HookPhase.LLM_OUTPUT,
            handler=_capture_output,
            name="capture_llm_output",
        )
    )

    runner = AgentRunner(agent=_EchoAgent(), session_manager=manager, hook_system=hooks)
    session_key = "agent:main:telegram:dm:user-500"
    await _collect_events(runner, session_key, "触发钩子", SkillDeps(peer_id="user-500", session_key=session_key))

    assert len(llm_inputs) >= 1
    assert llm_inputs[0]["user_message"] == "触发钩子"
    assert any("回复:触发钩子" in item.get("content", "") for item in llm_outputs)


@pytest.mark.asyncio
async def test_chat_compaction_result_reinjected_to_next_turn_context(tmp_path):
    """压缩后摘要应回写会话，并在下一轮作为 message_history 回注。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    agent = _EchoAgent()
    compaction = _SingleShotCompactionPipeline()
    runner = AgentRunner(agent=agent, session_manager=manager, compaction=compaction)

    session_key = "agent:main:telegram:dm:user-600"
    await _collect_events(runner, session_key, "第一轮", SkillDeps(peer_id="user-600", session_key=session_key))
    second_events = await _collect_events(
        runner,
        session_key,
        "第二轮",
        SkillDeps(peer_id="user-600", session_key=session_key),
    )

    assert compaction.compact_calls == 1
    assert any(e.type == "compaction" and e.phase == "start" for e in second_events)
    assert any(e.type == "compaction" and e.phase == "end" for e in second_events)

    second_history = agent.calls[1]["message_history"]
    assert second_history[0]["role"] == "system"
    assert second_history[0]["content"].startswith("[压缩摘要]")

    transcript = await manager.load_transcript(session_key)
    assert transcript[0].role == "system"
    assert transcript[0].content.startswith("[压缩摘要]")
    assert transcript[-2].content == "第二轮"
    assert transcript[-1].content == "回复:第二轮"


@pytest.mark.asyncio
async def test_chat_inloop_compaction_keeps_messages_after_compaction_point(tmp_path):
    """运行中压缩后，压缩点之后新增消息仍应持久化。"""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    agent = _GrowingMessagesAgent()
    compaction = _InLoopSingleShotCompactionPipeline()
    runner = AgentRunner(agent=agent, session_manager=manager, compaction=compaction)

    session_key = "agent:main:telegram:dm:user-700"
    # 预置历史，确保 should_compact 在 loop 前先被检查一次。
    await manager.persist_transcript(session_key, [{"role": "user", "content": "上一轮上下文"}])
    events = await _collect_events(
        runner,
        session_key,
        "排查问题",
        SkillDeps(peer_id="user-700", session_key=session_key),
    )

    assert any(e.type == "compaction" and e.phase == "start" for e in events)
    assert any(e.type == "compaction" and e.phase == "end" for e in events)

    transcript = await manager.load_transcript(session_key)
    assert transcript[0].role == "system"
    assert transcript[0].content.startswith("[压缩摘要]")
    assert transcript[-1].role == "assistant"
    assert transcript[-1].content == "已完成处理"


@pytest.mark.asyncio
async def test_chat_structured_final_messages_emit_and_persist_assistant_text(tmp_path):
    """Convert final ModelResponse.parts text into assistant events and persisted transcript."""
    manager = SessionManager(agents_dir=str(tmp_path), agent_id="main")
    runner = AgentRunner(agent=_StructuredFinalMessageAgent(), session_manager=manager)

    session_key = "agent:main:telegram:dm:user-structured"
    events = await _collect_events(
        runner,
        session_key,
        "hi",
        SkillDeps(peer_id="user-structured", session_key=session_key),
    )

    assistant_events = [e for e in events if e.type == "assistant"]
    assert [e.content for e in assistant_events] == ["reply:hi"]

    transcript = await manager.load_transcript(session_key)
    assert transcript[-1].role == "assistant"
    assert transcript[-1].content == "reply:hi"
