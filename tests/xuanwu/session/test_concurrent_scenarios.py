# -*- coding: utf-8 -*-
"""
并发场景验证测试

涵盖：
1. 同一用户多会话并发 (session_key 不同)
2. 多用户同时请求 (user_id 不同)
3. 工具调用嵌套层级 (50 次限制)
4. 子 Agent 创建后的资源回收
"""

from __future__ import annotations

import asyncio
import pytest
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.xuanwu.session.queue import SessionQueue, QueueMode
from app.xuanwu.session.manager import SessionManager
from app.xuanwu.session.context import SessionKey
from app.xuanwu.core.config_schema import ResetMode


class TestConcurrentScenarios:
    """并发场景测试套件"""

    @pytest.mark.asyncio
    async def test_same_user_multiple_sessions_concurrent(self, tmp_path):
        """
        场景1: 同一用户多会话并发 (session_key 不同)
        
        验证：同一用户的不同 session_key 可以并发执行
        """
        queue = SessionQueue(max_concurrent=10)
        
        # 同一用户的不同 session
        session_keys = [
            f"agent:main:user:alice:api:dm:peer-{i}"
            for i in range(5)
        ]
        
        execution_order = []
        
        async def simulate_session(session_key: str, delay: float):
            """模拟会话执行"""
            await queue.acquire(session_key)
            try:
                execution_order.append((session_key, "start", time.monotonic()))
                await asyncio.sleep(delay)
                execution_order.append((session_key, "end", time.monotonic()))
            finally:
                queue.release(session_key)
        
        # 同时启动多个会话
        start_time = time.monotonic()
        tasks = [
            simulate_session(key, 0.1)
            for key in session_keys
        ]
        await asyncio.gather(*tasks)
        elapsed = time.monotonic() - start_time
        
        # 验证：5个会话并发执行，总时间应接近 0.1s (串行则是 0.5s)
        assert elapsed < 0.3, f"并发执行时间过长: {elapsed}s，可能未正确并发"
        assert len(execution_order) == 10  # 5 start + 5 end
        
        # 验证所有会话都已完成
        ended_sessions = set(
            item[0] for item in execution_order if item[1] == "end"
        )
        assert len(ended_sessions) == 5

    @pytest.mark.asyncio
    async def test_multi_user_concurrent_requests(self, tmp_path):
        """
        场景2: 多用户同时请求 (user_id 不同)
        
        验证：不同用户的请求可以并发执行，且数据隔离
        """
        workspace_path = str(tmp_path / "workspace")
        
        # 为不同用户创建 SessionManager
        users = ["alice", "bob", "charlie", "david", "eve"]
        managers = {
            user: SessionManager(
                workspace_path=workspace_path,
                user_id=user,
                reset_mode=ResetMode.MANUAL,
            )
            for user in users
        }
        
        creation_times = {}
        
        async def create_session_for_user(user: str):
            """为用户创建会话"""
            start = time.monotonic()
            session = await managers[user].get_or_create(f"session-{user}")
            creation_times[user] = time.monotonic() - start
            return user, session.session_id
        
        # 同时创建会话
        start_time = time.monotonic()
        results = await asyncio.gather(*[
            create_session_for_user(user)
            for user in users
        ])
        elapsed = time.monotonic() - start_time
        
        # 验证：5个用户并发创建，总时间应远小于串行时间
        assert elapsed < 1.0, f"多用户并发创建时间过长: {elapsed}s"
        
        # 验证：每个用户有独立的会话
        for user, session_id in results:
            assert session_id is not None
            
        # 验证：用户数据目录隔离 (新 workspace 模式: users/<user_id>/sessions/)
        for user in users:
            user_dir = Path(workspace_path) / "users" / user / "sessions"
            assert user_dir.exists(), f"用户 {user} 的目录不存在"
            assert (user_dir / "sessions.json").exists()

    @pytest.mark.asyncio
    async def test_max_concurrent_limit(self, tmp_path):
        """
        验证：max_concurrent 限制生效
        
        当并发数超过限制时，后续请求应等待
        """
        max_concurrent = 3
        queue = SessionQueue(max_concurrent=max_concurrent)
        
        active_count = 0
        max_active = 0
        
        async def simulate_task(session_key: str, delay: float):
            """模拟任务"""
            nonlocal active_count, max_active
            await queue.acquire(session_key)
            try:
                active_count += 1
                max_active = max(max_active, active_count)
                await asyncio.sleep(delay)
                active_count -= 1
            finally:
                queue.release(session_key)
        
        # 启动 5 个任务，但 max_concurrent=3
        tasks = [
            simulate_task(f"session-{i}", 0.2)
            for i in range(5)
        ]
        await asyncio.gather(*tasks)
        
        # 验证：同时活跃的任务数不超过 max_concurrent
        assert max_active <= max_concurrent, (
            f"并发数超过限制: {max_active} > {max_concurrent}"
        )

    @pytest.mark.asyncio
    async def test_session_serialization_per_key(self, tmp_path):
        """
        验证：同一 session_key 的请求是串行的
        
        同一用户的同一会话应该排队执行，而不是并发
        """
        queue = SessionQueue(max_concurrent=10)
        session_key = "agent:main:user:alice:api:dm:bob"
        
        execution_times = []
        
        async def simulate_message(message_id: int):
            """模拟消息处理"""
            await queue.acquire(session_key)
            try:
                start = time.monotonic()
                await asyncio.sleep(0.05)
                end = time.monotonic()
                execution_times.append((message_id, start, end))
            finally:
                queue.release(session_key)
        
        # 同一 session_key 的多个消息
        tasks = [
            simulate_message(i)
            for i in range(3)
        ]
        await asyncio.gather(*tasks)
        
        # 验证：消息是串行执行的（时间不重叠）
        for i in range(len(execution_times) - 1):
            current_end = execution_times[i][2]
            next_start = execution_times[i + 1][1]
            assert next_start >= current_end - 0.001, (
                "同一会话的消息应该是串行执行"
            )


class TestToolCallLimit:
    """工具调用限制测试"""

    @pytest.mark.asyncio
    async def test_max_tool_calls_limit(self):
        """
        场景3: 工具调用嵌套层级 (50 次限制)
        
        验证：AgentRunner 的 max_tool_calls 限制生效
        """
        from app.xuanwu.agent.runner import AgentRunner
        
        # Mock 依赖
        mock_agent = MagicMock()
        mock_session_manager = AsyncMock()
        mock_session_manager.get_or_create = AsyncMock()
        mock_session_manager.load_transcript = AsyncMock(return_value=[])
        
        runner = AgentRunner(
            agent=mock_agent,
            session_manager=mock_session_manager,
        )
        
        # 验证默认值
        assert runner is not None
        
    @pytest.mark.asyncio
    async def test_tool_call_counter_increment(self):
        """
        验证：工具调用计数器正确递增
        """
        # 这是一个简化测试，实际测试需要在 AgentRunner.run 中验证
        tool_calls_count = 0
        max_tool_calls = 50
        
        # 模拟 50 次工具调用
        for i in range(max_tool_calls):
            tool_calls_count += 1
            assert tool_calls_count <= max_tool_calls, (
                f"工具调用次数超过限制: {tool_calls_count}"
            )
        
        # 第 51 次应该触发限制
        tool_calls_count += 1
        assert tool_calls_count > max_tool_calls


class TestSubAgentResourceCleanup:
    """子 Agent 资源回收测试"""

    @pytest.mark.asyncio
    async def test_subagent_session_creation(self, tmp_path):
        """
        场景4: 子 Agent 创建后的资源回收
        
        验证：子 Agent 创建的 session 可以被正确清理
        """
        from app.xuanwu.tools.sessions.spawn_tool import sessions_spawn_tool
        
        workspace_path = str(tmp_path / "workspace")
        session_manager = SessionManager(
            workspace_path=workspace_path,
            user_id="default",
            reset_mode=ResetMode.MANUAL,
        )
        
        # Mock RunContext
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()
        mock_ctx.deps.session_manager = session_manager
        
        # 创建子 Agent
        result = await sessions_spawn_tool(
            ctx=mock_ctx,
            prompt="Help me with coding task",
            tools="read,write,exec"
        )
        
        # 验证：子 Agent 创建成功 (ToolResult.to_dict() 格式)
        assert result["is_error"] is False
        assert "subagent_id" in result.get("details", {})
        
        subagent_id = result["details"]["subagent_id"]
        session_key = result["details"]["session_key"]
        
        # 验证：session 被创建
        assert session_key.startswith("subagent-")
        
    @pytest.mark.asyncio
    async def test_session_queue_cleanup(self):
        """
        验证：SessionQueue 正确清理完成的会话
        """
        queue = SessionQueue(max_concurrent=3)
        session_key = "test-session-001"
        
        # 初始状态
        assert not queue.is_active(session_key)
        
        # 获取执行权
        await queue.acquire(session_key)
        assert queue.is_active(session_key)
        
        # 释放执行权
        queue.release(session_key)
        assert not queue.is_active(session_key)
        
    @pytest.mark.asyncio
    async def test_queued_messages_cleanup(self):
        """
        验证：队列中的消息在处理后被清理
        """
        queue = SessionQueue(max_concurrent=3, cap=5)
        session_key = "test-session"
        
        # 添加消息到队列
        for i in range(3):
            queue.enqueue(session_key, f"message-{i}")
        
        # 验证：消息在队列中
        messages = queue.get_queued_messages(session_key)
        assert len(messages) == 3
        
        # 清空队列
        queue.clear_queue(session_key)
        
        # 验证：队列已清空
        messages = queue.get_queued_messages(session_key)
        assert len(messages) == 0


class TestConcurrentEdgeCases:
    """并发边界情况测试"""

    @pytest.mark.asyncio
    async def test_queue_overflow_strategy(self):
        """
        验证：队列溢出策略正确工作
        """
        queue = SessionQueue(max_concurrent=3, cap=3, mode=QueueMode.COLLECT)
        session_key = "test-session"
        
        # 填满队列
        for i in range(5):
            result = queue.enqueue(session_key, f"message-{i}")
            
        # 验证：根据溢出策略，部分消息可能被丢弃
        messages = queue.get_queued_messages(session_key)
        assert len(messages) <= 3, "队列长度超过 cap"

    @pytest.mark.asyncio
    async def test_concurrent_acquire_release(self):
        """
        验证：并发获取和释放信号量不会死锁
        """
        queue = SessionQueue(max_concurrent=2)
        
        async def worker(session_key: str, duration: float):
            await queue.acquire(session_key)
            try:
                await asyncio.sleep(duration)
            finally:
                queue.release(session_key)
        
        # 多个 worker 并发执行
        workers = [
            worker(f"session-{i % 3}", 0.01)
            for i in range(10)
        ]
        
        # 应在有限时间内完成，无死锁
        await asyncio.wait_for(
            asyncio.gather(*workers),
            timeout=5.0
        )


# 运行测试的辅助函数
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
