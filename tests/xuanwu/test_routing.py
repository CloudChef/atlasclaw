# -*- coding: utf-8 -*-
"""
多智能体路由模块单元测试

测试 AgentRouter、BindingRule、DmScope、ToolPolicy、AgentRouterFactory 等组件。
"""

import pytest

from app.xuanwu.agent.routing import (
    AgentConfig,
    AgentRouter,
    AgentRouterFactory,
    BindingRule,
    DmScope,
    RoutingContext,
    SandboxMode,
    ToolPolicy,
)


class TestToolPolicy:
    """ToolPolicy 测试类"""

    def test_default_allow_all(self):
        """默认策略允许所有工具"""
        policy = ToolPolicy()
        assert policy.is_allowed("search")
        assert policy.is_allowed("bash")
        assert policy.is_allowed("read_file")

    def test_deny_overrides_allow(self):
        """拒绝规则优先于允许规则"""
        policy = ToolPolicy(allow=["*"], deny=["bash", "shell"])
        assert policy.is_allowed("search")
        assert not policy.is_allowed("bash")
        assert not policy.is_allowed("shell")

    def test_wildcard_prefix(self):
        """测试前缀通配符匹配"""
        policy = ToolPolicy(allow=["read_*"], deny=[])
        assert policy.is_allowed("read_file")
        assert policy.is_allowed("read_dir")
        assert not policy.is_allowed("write_file")

    def test_wildcard_suffix(self):
        """测试后缀通配符匹配"""
        policy = ToolPolicy(allow=["*_file"], deny=[])
        assert policy.is_allowed("read_file")
        assert policy.is_allowed("write_file")
        assert not policy.is_allowed("read_dir")

    def test_exact_match(self):
        """测试精确匹配"""
        policy = ToolPolicy(allow=["search", "calculate"], deny=[])
        assert policy.is_allowed("search")
        assert policy.is_allowed("calculate")
        assert not policy.is_allowed("bash")

    def test_empty_allow_denies_all(self):
        """空允许列表拒绝所有"""
        policy = ToolPolicy(allow=[], deny=[])
        assert not policy.is_allowed("anything")


class TestAgentConfig:
    """AgentConfig 测试类"""

    def test_create_config(self):
        """测试创建配置"""
        config = AgentConfig(id="test-agent", model="gpt-4o")
        assert config.id == "test-agent"
        assert config.model == "gpt-4o"
        assert config.sandbox == SandboxMode.OFF
        assert config.dm_scope == DmScope.MAIN

    def test_default_config(self):
        """测试默认配置"""
        config = AgentConfig.default()
        assert config.id == "main"
        assert config.model == "gpt-4o"

    def test_config_with_tools(self):
        """测试带工具策略的配置"""
        tools = ToolPolicy(allow=["search"], deny=["bash"])
        config = AgentConfig(id="safe", tools=tools)
        assert config.tools.is_allowed("search")
        assert not config.tools.is_allowed("bash")


class TestBindingRule:
    """BindingRule 测试类"""

    def test_match_all(self):
        """测试无条件匹配"""
        rule = BindingRule(agent_id="main")
        ctx = RoutingContext(peer_id="user1", channel="telegram")
        assert rule.matches(ctx)

    def test_match_by_peer(self):
        """测试按对话者匹配"""
        rule = BindingRule(agent_id="support", peer="user-vip")
        ctx_match = RoutingContext(peer_id="user-vip", channel="telegram")
        ctx_no_match = RoutingContext(peer_id="user-normal", channel="telegram")
        assert rule.matches(ctx_match)
        assert not rule.matches(ctx_no_match)

    def test_match_by_channel(self):
        """测试按通道匹配"""
        rule = BindingRule(agent_id="slack-bot", channel="slack")
        ctx_match = RoutingContext(peer_id="u1", channel="slack")
        ctx_no_match = RoutingContext(peer_id="u1", channel="telegram")
        assert rule.matches(ctx_match)
        assert not rule.matches(ctx_no_match)

    def test_match_by_guild_id(self):
        """测试按群组匹配"""
        rule = BindingRule(agent_id="ops", guild_id="guild-123")
        ctx = RoutingContext(peer_id="u1", channel="discord", guild_id="guild-123")
        assert rule.matches(ctx)

    def test_multi_field_match(self):
        """测试多字段匹配"""
        rule = BindingRule(agent_id="bot", channel="telegram", peer="vip-user")
        ctx_both = RoutingContext(peer_id="vip-user", channel="telegram")
        ctx_partial = RoutingContext(peer_id="vip-user", channel="slack")
        assert rule.matches(ctx_both)
        assert not rule.matches(ctx_partial)

    def test_specificity_scoring(self):
        """测试特异性评分"""
        rule_generic = BindingRule(agent_id="a", channel="telegram")
        rule_specific = BindingRule(agent_id="b", channel="telegram", peer="user1")
        assert rule_specific.specificity() > rule_generic.specificity()

    def test_specificity_with_priority(self):
        """测试优先级影响评分"""
        rule_low = BindingRule(agent_id="a", channel="telegram", priority=0)
        rule_high = BindingRule(agent_id="b", channel="telegram", priority=100)
        assert rule_high.specificity() > rule_low.specificity()


class TestAgentRouter:
    """AgentRouter 测试类"""

    def test_single_agent_mode(self):
        """测试单智能体模式"""
        router = AgentRouter(single_agent_mode=True)
        ctx = RoutingContext(peer_id="u1", channel="api")
        agent = router.route(ctx)
        assert agent.id == "main"

    def test_register_agent(self):
        """测试注册智能体"""
        router = AgentRouter()
        config = AgentConfig(id="support", model="gpt-4o-mini")
        router.register_agent(config)
        assert router.get_agent("support") is not None
        assert router.get_agent("support").model == "gpt-4o-mini"

    def test_multi_agent_mode_auto(self):
        """注册多个智能体自动切换到多智能体模式"""
        router = AgentRouter(single_agent_mode=True)
        router.register_agent(AgentConfig(id="support"))
        assert not router.single_agent_mode

    def test_unregister_agent(self):
        """测试注销智能体"""
        router = AgentRouter(single_agent_mode=False)
        router.register_agent(AgentConfig(id="test"))
        assert router.unregister_agent("test")
        assert router.get_agent("test") is None

    def test_unregister_removes_bindings(self):
        """注销智能体同时移除绑定"""
        router = AgentRouter(single_agent_mode=False)
        router.register_agent(AgentConfig(id="bot"))
        router.add_binding(BindingRule(agent_id="bot", channel="slack"))
        router.unregister_agent("bot")
        # 路由不应再命中已注销的智能体
        ctx = RoutingContext(peer_id="u1", channel="slack")
        agent = router.route(ctx)
        assert agent.id != "bot"

    def test_list_agents(self):
        """测试列出智能体"""
        router = AgentRouter(single_agent_mode=False)
        router.register_agent(AgentConfig(id="a1"))
        router.register_agent(AgentConfig(id="a2"))
        agents = router.list_agents()
        assert len(agents) >= 2

    def test_add_binding_unknown_agent(self):
        """绑定未知智能体抛异常"""
        router = AgentRouter(single_agent_mode=False)
        with pytest.raises(ValueError, match="Unknown agent"):
            router.add_binding(BindingRule(agent_id="ghost", channel="api"))

    def test_route_by_binding(self):
        """测试绑定规则路由"""
        router = AgentRouter(single_agent_mode=False, default_agent_id="main")
        router.register_agent(AgentConfig(id="main"))
        router.register_agent(AgentConfig(id="support"))
        router.add_binding(BindingRule(agent_id="support", channel="telegram"))

        ctx_tg = RoutingContext(peer_id="u1", channel="telegram")
        ctx_api = RoutingContext(peer_id="u1", channel="api")

        assert router.route(ctx_tg).id == "support"
        assert router.route(ctx_api).id == "main"

    def test_route_specificity_order(self):
        """高特异性规则优先匹配"""
        router = AgentRouter(single_agent_mode=False, default_agent_id="main")
        router.register_agent(AgentConfig(id="main"))
        router.register_agent(AgentConfig(id="generic"))
        router.register_agent(AgentConfig(id="vip"))

        router.add_binding(BindingRule(agent_id="generic", channel="telegram"))
        router.add_binding(BindingRule(agent_id="vip", channel="telegram", peer="vip-user"))

        ctx_vip = RoutingContext(peer_id="vip-user", channel="telegram")
        ctx_normal = RoutingContext(peer_id="normal-user", channel="telegram")

        assert router.route(ctx_vip).id == "vip"
        assert router.route(ctx_normal).id == "generic"

    def test_route_fallback_to_default(self):
        """无匹配时回退到默认智能体"""
        router = AgentRouter(single_agent_mode=False, default_agent_id="main")
        router.register_agent(AgentConfig(id="main"))
        router.register_agent(AgentConfig(id="special"))
        router.add_binding(BindingRule(agent_id="special", channel="slack"))

        ctx = RoutingContext(peer_id="u1", channel="api")
        assert router.route(ctx).id == "main"

    def test_session_scope_main(self):
        """测试 MAIN 分组策略"""
        router = AgentRouter()
        agent = AgentConfig(id="main", dm_scope=DmScope.MAIN)
        ctx = RoutingContext(peer_id="u1", channel="telegram")
        assert router.get_session_scope(agent, ctx) == "main"

    def test_session_scope_per_peer(self):
        """测试 PER_PEER 分组策略"""
        router = AgentRouter()
        agent = AgentConfig(id="main", dm_scope=DmScope.PER_PEER)
        ctx = RoutingContext(peer_id="user-123", channel="telegram")
        scope = router.get_session_scope(agent, ctx)
        assert "user-123" in scope

    def test_session_scope_per_channel_peer(self):
        """测试 PER_CHANNEL_PEER 分组策略"""
        router = AgentRouter()
        agent = AgentConfig(id="main", dm_scope=DmScope.PER_CHANNEL_PEER)
        ctx = RoutingContext(peer_id="u1", channel="telegram")
        scope = router.get_session_scope(agent, ctx)
        assert "telegram" in scope
        assert "u1" in scope

    def test_session_scope_per_account_channel_peer(self):
        """测试 PER_ACCOUNT_CHANNEL_PEER 分组策略"""
        router = AgentRouter()
        agent = AgentConfig(id="main", dm_scope=DmScope.PER_ACCOUNT_CHANNEL_PEER)
        ctx = RoutingContext(peer_id="u1", channel="telegram", account_id="acc-1")
        scope = router.get_session_scope(agent, ctx)
        assert "acc-1" in scope
        assert "telegram" in scope
        assert "u1" in scope

    def test_check_tool_permission(self):
        """测试工具权限检查"""
        router = AgentRouter()
        agent = AgentConfig(id="main", tools=ToolPolicy(allow=["*"], deny=["bash"]))
        assert router.check_tool_permission(agent, "search")
        assert not router.check_tool_permission(agent, "bash")


class TestAgentRouterFactory:
    """AgentRouterFactory 测试类"""

    def test_from_config_single_agent(self):
        """测试单智能体配置"""
        config = {
            "agents": {
                "default": "main",
                "list": [{"id": "main", "model": "gpt-4o"}],
            }
        }
        router = AgentRouterFactory.from_config(config)
        assert router.single_agent_mode
        assert router.get_agent("main") is not None

    def test_from_config_multi_agent(self):
        """测试多智能体配置"""
        config = {
            "agents": {
                "default": "main",
                "list": [
                    {"id": "main", "model": "gpt-4o"},
                    {"id": "support", "model": "gpt-4o-mini"},
                ],
                "bindings": [
                    {"agentId": "support", "channel": "telegram"},
                ],
            }
        }
        router = AgentRouterFactory.from_config(config)
        assert not router.single_agent_mode
        assert router.get_agent("support") is not None

    def test_from_config_with_tools(self):
        """测试带工具策略的配置"""
        config = {
            "agents": {
                "list": [
                    {
                        "id": "main",
                        "tools": {"allow": ["*"], "deny": ["bash"]},
                    }
                ],
            }
        }
        router = AgentRouterFactory.from_config(config)
        agent = router.get_agent("main")
        assert not agent.tools.is_allowed("bash")

    def test_from_empty_config(self):
        """测试空配置"""
        router = AgentRouterFactory.from_config({})
        # 应该返回一个可用的路由器
        ctx = RoutingContext(peer_id="u1", channel="api")
        agent = router.route(ctx)
        assert agent is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
