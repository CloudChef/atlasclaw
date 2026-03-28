# -*- coding: utf-8 -*-
"""
多租户模块单元测试

测试 TenantConfig、TenantUsage、TenantIsolation、TenantManager 等组件。
"""

import pytest

from app.xuanwu.core.tenant import (
    TenantConfig,
    TenantIsolation,
    TenantManager,
    TenantUsage,
)


class TestTenantConfig:
    """TenantConfig 测试类"""

    def test_create_config(self):
        """测试创建配置"""
        config = TenantConfig(id="acme", name="Acme Corp")
        assert config.id == "acme"
        assert config.name == "Acme Corp"
        assert config.enabled

    def test_model_allowed_no_restriction(self):
        """无限制时允许所有模型"""
        config = TenantConfig(id="test", name="Test")
        assert config.is_model_allowed("gpt-4o")
        assert config.is_model_allowed("claude-3-sonnet")

    def test_model_allowed_with_restriction(self):
        """有限制时只允许列表中的模型"""
        config = TenantConfig(
            id="test", name="Test",
            allowed_models=["gpt-4o", "gpt-4o-mini"],
        )
        assert config.is_model_allowed("gpt-4o")
        assert not config.is_model_allowed("claude-3-sonnet")

    def test_feature_enabled_default(self):
        """功能默认启用"""
        config = TenantConfig(id="test", name="Test")
        assert config.is_feature_enabled("memory")
        assert config.is_feature_enabled("tts")

    def test_feature_disabled(self):
        """功能可被禁用"""
        config = TenantConfig(
            id="test", name="Test",
            features={"tts": False, "vision": True},
        )
        assert not config.is_feature_enabled("tts")
        assert config.is_feature_enabled("vision")


class TestTenantUsage:
    """TenantUsage 测试类"""

    def test_check_quota_ok(self):
        """测试配额正常"""
        config = TenantConfig(id="test", name="Test", max_sessions=100)
        usage = TenantUsage(tenant_id="test", session_count=50)
        ok, reason = usage.check_quota(config)
        assert ok
        assert reason == ""

    def test_check_quota_session_exceeded(self):
        """测试会话配额超限"""
        config = TenantConfig(id="test", name="Test", max_sessions=10)
        usage = TenantUsage(tenant_id="test", session_count=10)
        ok, reason = usage.check_quota(config)
        assert not ok
        assert "Session" in reason

    def test_check_quota_agent_exceeded(self):
        """测试智能体配额超限"""
        config = TenantConfig(id="test", name="Test", max_agents=5)
        usage = TenantUsage(tenant_id="test", agent_count=5)
        ok, reason = usage.check_quota(config)
        assert not ok
        assert "Agent" in reason

    def test_check_quota_memory_exceeded(self):
        """测试记忆配额超限"""
        config = TenantConfig(id="test", name="Test", max_memory_entries=100)
        usage = TenantUsage(tenant_id="test", memory_entry_count=100)
        ok, reason = usage.check_quota(config)
        assert not ok
        assert "Memory" in reason

    def test_check_quota_concurrent_exceeded(self):
        """测试并发运行配额超限"""
        config = TenantConfig(id="test", name="Test", max_concurrent_runs=5)
        usage = TenantUsage(tenant_id="test", active_runs=5)
        ok, reason = usage.check_quota(config)
        assert not ok
        assert "Concurrent" in reason


class TestTenantIsolation:
    """TenantIsolation 测试类"""

    def test_session_prefix(self):
        """测试会话前缀"""
        prefix = TenantIsolation.get_session_prefix("acme")
        assert prefix == "tenant:acme:"

    def test_memory_path(self):
        """测试记忆存储路径"""
        path = TenantIsolation.get_memory_path("acme", "/data")
        assert "tenants" in path
        assert "acme" in path
        assert "memory" in path

    def test_session_path(self):
        """测试会话存储路径"""
        path = TenantIsolation.get_session_path("acme", "/data")
        assert "acme" in path
        assert "sessions" in path

    def test_isolate_session_key(self):
        """测试会话密钥隔离"""
        key = TenantIsolation.isolate_session_key("acme", "agent:main:api:dm:u1")
        assert key == "tenant:acme:agent:main:api:dm:u1"

    def test_isolate_already_isolated(self):
        """已隔离的密钥不重复处理"""
        key = TenantIsolation.isolate_session_key("acme", "tenant:acme:agent:main:api:dm:u1")
        assert key == "tenant:acme:agent:main:api:dm:u1"

    def test_extract_tenant_id(self):
        """测试提取租户 ID"""
        tenant_id = TenantIsolation.extract_tenant_id("tenant:acme:agent:main")
        assert tenant_id == "acme"

    def test_extract_tenant_id_none(self):
        """非租户密钥返回 None"""
        tenant_id = TenantIsolation.extract_tenant_id("agent:main:api:dm:u1")
        assert tenant_id is None


class TestTenantManager:
    """TenantManager 测试类"""

    @pytest.mark.asyncio
    async def test_default_tenant(self):
        """测试默认租户"""
        mgr = TenantManager()
        config = await mgr.get("default")
        assert config is not None
        assert config.name == "Default Tenant"

    @pytest.mark.asyncio
    async def test_register_tenant(self):
        """测试注册租户"""
        mgr = TenantManager()
        await mgr.register(TenantConfig(id="acme", name="Acme Corp"))
        config = await mgr.get("acme")
        assert config is not None
        assert config.name == "Acme Corp"

    @pytest.mark.asyncio
    async def test_unregister_tenant(self):
        """测试注销租户"""
        mgr = TenantManager()
        await mgr.register(TenantConfig(id="temp", name="Temp"))
        assert await mgr.unregister("temp")
        assert await mgr.get("temp") is None

    @pytest.mark.asyncio
    async def test_cannot_unregister_default(self):
        """不能注销默认租户"""
        mgr = TenantManager()
        assert not await mgr.unregister("default")

    @pytest.mark.asyncio
    async def test_list_tenants(self):
        """测试列出租户"""
        mgr = TenantManager()
        await mgr.register(TenantConfig(id="t1", name="T1"))
        await mgr.register(TenantConfig(id="t2", name="T2"))
        tenants = await mgr.list_tenants()
        assert len(tenants) >= 3  # default + t1 + t2

    @pytest.mark.asyncio
    async def test_get_or_default(self):
        """测试获取或默认"""
        mgr = TenantManager()
        config = await mgr.get_or_default("nonexistent")
        assert config.id == "default"

    @pytest.mark.asyncio
    async def test_check_quota_ok(self):
        """测试配额检查正常"""
        mgr = TenantManager()
        ok, reason = await mgr.check_quota("default")
        assert ok

    @pytest.mark.asyncio
    async def test_check_quota_unknown_tenant(self):
        """测试未知租户配额检查"""
        mgr = TenantManager()
        ok, reason = await mgr.check_quota("ghost")
        assert not ok
        assert "Unknown" in reason

    @pytest.mark.asyncio
    async def test_check_quota_disabled_tenant(self):
        """测试禁用租户配额检查"""
        mgr = TenantManager()
        await mgr.register(TenantConfig(id="disabled", name="D", enabled=False))
        ok, reason = await mgr.check_quota("disabled")
        assert not ok
        assert "disabled" in reason.lower()

    @pytest.mark.asyncio
    async def test_record_session_lifecycle(self):
        """测试会话生命周期记录"""
        mgr = TenantManager()
        await mgr.record_session_created("default")
        usage = await mgr.get_usage("default")
        assert usage.session_count == 1

        await mgr.record_session_deleted("default")
        usage = await mgr.get_usage("default")
        assert usage.session_count == 0

    @pytest.mark.asyncio
    async def test_record_run_lifecycle(self):
        """测试运行生命周期记录"""
        mgr = TenantManager()
        await mgr.record_run_started("default")
        usage = await mgr.get_usage("default")
        assert usage.active_runs == 1
        assert usage.total_runs == 1

        await mgr.record_run_completed("default", tokens_used=500)
        usage = await mgr.get_usage("default")
        assert usage.active_runs == 0
        assert usage.total_tokens_used == 500

    @pytest.mark.asyncio
    async def test_record_memory_created(self):
        """测试记忆创建记录"""
        mgr = TenantManager()
        result = await mgr.record_memory_created("default")
        assert result is True
        usage = await mgr.get_usage("default")
        assert usage.memory_entry_count == 1

    @pytest.mark.asyncio
    async def test_resolve_tenant_from_session_key(self):
        """测试从会话密钥解析租户"""
        mgr = TenantManager()
        await mgr.register(TenantConfig(id="acme", name="Acme"))
        tenant_id = mgr.resolve_tenant(session_key="tenant:acme:agent:main")
        assert tenant_id == "acme"

    @pytest.mark.asyncio
    async def test_resolve_tenant_from_header(self):
        """测试从请求头解析租户"""
        mgr = TenantManager()
        await mgr.register(TenantConfig(id="acme", name="Acme"))
        tenant_id = mgr.resolve_tenant(
            request_headers={"X-Tenant-ID": "acme"},
        )
        assert tenant_id == "acme"

    @pytest.mark.asyncio
    async def test_resolve_tenant_fallback_default(self):
        """测试回退到默认租户"""
        mgr = TenantManager()
        tenant_id = mgr.resolve_tenant()
        assert tenant_id == "default"

    @pytest.mark.asyncio
    async def test_isolate_session_key(self):
        """测试会话密钥隔离"""
        mgr = TenantManager(enable_isolation=True)
        key = mgr.isolate_session_key("acme", "agent:main")
        assert "tenant:acme:" in key

    @pytest.mark.asyncio
    async def test_isolate_disabled(self):
        """隔离禁用时不修改密钥"""
        mgr = TenantManager(enable_isolation=False)
        key = mgr.isolate_session_key("acme", "agent:main")
        assert key == "agent:main"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
