# -*- coding: utf-8 -*-
"""
执行上下文模块单元测试

测试 ExecutionContext、SecurityPolicy、FileAccessPolicy、NetworkAccessPolicy 等组件。
"""

import time

import pytest

from app.xuanwu.core.execution_context import (
    ExecutionContext,
    ExecutionContextManager,
    FileAccessPolicy,
    NetworkAccessPolicy,
    ResourceLimit,
    SandboxMode,
    SecurityPolicy,
)


class TestFileAccessPolicy:
    """FileAccessPolicy 测试类"""

    def test_default_allows_read(self):
        """默认策略允许读取"""
        policy = FileAccessPolicy()
        assert policy.can_read("/some/file.txt")

    def test_default_denies_write(self):
        """默认策略拒绝写入"""
        policy = FileAccessPolicy()
        assert not policy.can_write("/some/file.txt")

    def test_deny_overrides_read(self):
        """拒绝规则覆盖读取允许"""
        policy = FileAccessPolicy(
            allow_read=["*"],
            deny=["/etc/*", "/root/*"],
        )
        assert policy.can_read("/home/user/file.txt")
        assert not policy.can_read("/etc/passwd")
        assert not policy.can_read("/root/.ssh/id_rsa")

    def test_allow_write_specific(self):
        """允许特定路径写入"""
        policy = FileAccessPolicy(
            allow_write=["./workspace/*"],
        )
        assert policy.can_write("./workspace/output.txt")
        assert not policy.can_write("/tmp/output.txt")


class TestNetworkAccessPolicy:
    """NetworkAccessPolicy 测试类"""

    def test_default_allows_most(self):
        """默认策略允许大部分主机"""
        policy = NetworkAccessPolicy()
        assert policy.can_connect("api.openai.com", 443)

    def test_default_denies_ssh(self):
        """默认策略拒绝 SSH 端口"""
        policy = NetworkAccessPolicy()
        assert not policy.can_connect("example.com", 22)
        assert not policy.can_connect("example.com", 3389)

    def test_deny_host(self):
        """测试主机拒绝"""
        policy = NetworkAccessPolicy(deny_hosts=["internal.corp.com"])
        assert not policy.can_connect("internal.corp.com", 443)
        assert policy.can_connect("api.openai.com", 443)

    def test_wildcard_host(self):
        """测试域名通配符"""
        policy = NetworkAccessPolicy(
            allow_hosts=["*.openai.com", "api.anthropic.com"],
        )
        assert policy.can_connect("api.openai.com", 443)
        assert policy.can_connect("chat.openai.com", 443)
        assert policy.can_connect("api.anthropic.com", 443)
        assert not policy.can_connect("evil.com", 443)

    def test_allowed_ports(self):
        """测试端口白名单"""
        policy = NetworkAccessPolicy(allow_ports=[80, 443])
        assert policy.can_connect("example.com", 80)
        assert policy.can_connect("example.com", 443)
        assert not policy.can_connect("example.com", 8080)


class TestSecurityPolicy:
    """SecurityPolicy 测试类"""

    def test_tool_allowed(self):
        """测试工具权限"""
        policy = SecurityPolicy(tools_allow=["*"], tools_deny=["bash", "shell"])
        assert policy.is_tool_allowed("search")
        assert not policy.is_tool_allowed("bash")

    def test_filter_env_vars(self):
        """测试环境变量过滤"""
        policy = SecurityPolicy()
        env = {
            "HOME": "/home/user",
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-xxx",
            "DB_PASSWORD": "secret",
            "MY_TOKEN": "tok-yyy",
        }
        filtered = policy.filter_env_vars(env)
        assert "HOME" in filtered
        assert "OPENAI_API_KEY" not in filtered
        assert "DB_PASSWORD" not in filtered
        assert "MY_TOKEN" not in filtered

    def test_permissive_policy(self):
        """测试宽松策略"""
        policy = SecurityPolicy.permissive()
        assert policy.is_tool_allowed("bash")
        assert policy.file_access.can_read("/any/path")
        assert policy.file_access.can_write("/any/path")

    def test_restrictive_policy(self):
        """测试严格策略"""
        policy = SecurityPolicy.restrictive()
        assert not policy.is_tool_allowed("bash")
        assert policy.is_tool_allowed("read_file")
        assert not policy.network_access.can_connect("evil.com", 443)


class TestExecutionContext:
    """ExecutionContext 测试类"""

    def test_create_context(self):
        """测试创建上下文"""
        ctx = ExecutionContext(
            agent_id="main",
            session_key="agent:main:api:dm:user-1",
        )
        assert ctx.agent_id == "main"
        assert ctx.started_at > 0
        assert ctx.timeout_at > ctx.started_at

    def test_can_use_tool(self):
        """测试工具权限检查"""
        policy = SecurityPolicy(tools_allow=["search"], tools_deny=["bash"])
        ctx = ExecutionContext(
            agent_id="main",
            session_key="test",
            security_policy=policy,
        )
        assert ctx.can_use_tool("search")
        assert not ctx.can_use_tool("bash")

    def test_aborted_blocks_all(self):
        """中止后所有操作被阻止"""
        ctx = ExecutionContext(agent_id="main", session_key="test")
        ctx.abort()
        assert not ctx.can_use_tool("search")
        assert not ctx.can_read_file("/file")
        assert not ctx.can_write_file("/file")
        assert not ctx.can_connect("host", 443)

    def test_sandbox_write_restriction(self):
        """沙箱模式限制写入"""
        policy = SecurityPolicy(
            file_access=FileAccessPolicy(allow_write=["*"]),
        )
        ctx = ExecutionContext(
            agent_id="main",
            session_key="test",
            sandbox_mode=SandboxMode.AGENT,
            security_policy=policy,
            workspace="/workspace",
        )
        # 工作区内可写
        assert ctx.can_write_file("/workspace/output.txt")

    def test_record_file_created(self):
        """测试记录文件创建"""
        ctx = ExecutionContext(agent_id="main", session_key="test")
        assert ctx.files_created == 0
        assert ctx.record_file_created()
        assert ctx.files_created == 1

    def test_record_connection(self):
        """测试记录连接"""
        ctx = ExecutionContext(agent_id="main", session_key="test")
        assert ctx.network_connections == 0
        assert ctx.record_connection()
        assert ctx.network_connections == 1

    def test_check_resources_ok(self):
        """测试资源检查通过"""
        ctx = ExecutionContext(agent_id="main", session_key="test")
        ok, reason = ctx.check_resources()
        assert ok
        assert reason == ""

    def test_check_resources_memory_exceeded(self):
        """测试内存超限"""
        ctx = ExecutionContext(agent_id="main", session_key="test")
        ctx.memory_used_mb = 999999.0
        ok, reason = ctx.check_resources()
        assert not ok
        assert "Memory" in reason

    def test_check_resources_files_exceeded(self):
        """测试文件数超限"""
        ctx = ExecutionContext(agent_id="main", session_key="test")
        ctx.files_created = 999999
        ok, reason = ctx.check_resources()
        assert not ok
        assert "File" in reason

    def test_to_dict(self):
        """测试序列化"""
        ctx = ExecutionContext(
            agent_id="main",
            session_key="test",
            sandbox_mode=SandboxMode.AGENT,
        )
        d = ctx.to_dict()
        assert d["agent_id"] == "main"
        assert d["sandbox_mode"] == "agent"


class TestExecutionContextManager:
    """ExecutionContextManager 测试类"""

    @pytest.mark.asyncio
    async def test_create_and_get(self):
        """测试创建和获取"""
        mgr = ExecutionContextManager()
        ctx = await mgr.create("main", "session-1")
        assert ctx.agent_id == "main"

        retrieved = await mgr.get("session-1")
        assert retrieved is ctx

    @pytest.mark.asyncio
    async def test_remove(self):
        """测试移除"""
        mgr = ExecutionContextManager()
        await mgr.create("main", "session-1")
        assert await mgr.remove("session-1")
        assert await mgr.get("session-1") is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self):
        """测试移除不存在的上下文"""
        mgr = ExecutionContextManager()
        assert not await mgr.remove("nonexistent")

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        """测试清理过期上下文"""
        mgr = ExecutionContextManager()
        policy = SecurityPolicy(
            resource_limit=ResourceLimit(timeout_seconds=0),
        )
        await mgr.create(
            "main", "expired-session", security_policy=policy,
        )
        # 上下文应该已过期
        count = await mgr.cleanup_expired()
        assert count == 1
        assert await mgr.get("expired-session") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
