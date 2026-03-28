# -*- coding: utf-8 -*-
"""

Multi-agent routing system

Implements agent definitions, binding-based routing, and grouping strategies.
Matches section 4.3 in `design.md` and `tasks.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field


class DmScope(Enum):
    """Direct-message grouping strategy"""
    MAIN = "main"  # session
    PER_PEER = "per-peer"  # peer session
    PER_CHANNEL_PEER = "per-channel-peer"  # channel peer
    PER_ACCOUNT_CHANNEL_PEER = "per-account-channel-peer"  # account channel peer


class SandboxMode(Enum):
    """Sandbox mode"""
    OFF = "off"  # 
    AGENT = "agent"  # agent
    SESSION = "session"  # session


@dataclass
class ToolPolicy:
    """


tool
 
 Attributes:
 allow:tool list(support *)
 deny:tool list(higher priority than allow)
 
"""
    allow: list[str] = field(default_factory=lambda: ["*"])
    deny: list[str] = field(default_factory=list)
    
    def is_allowed(self, tool_name: str) -> bool:
        """check tool"""
        # 
        for pattern in self.deny:
            if self._match_pattern(pattern, tool_name):
                return False
        
        # check
        for pattern in self.allow:
            if self._match_pattern(pattern, tool_name):
                return True
        
        return False
    
    def _match_pattern(self, pattern: str, name: str) -> bool:
        """mode"""
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return name.startswith(pattern[:-1])
        if pattern.startswith("*"):
            return name.endswith(pattern[1:])
        return pattern == name


@dataclass
class AgentConfig:
    """


agent configuration
 
 Attributes:
 id:agent ID
 workspace:workspace path
 agent_dir:agent
 model:usemodel
 tools:tool
 sandbox:Sandbox mode
 dm_scope:Direct-message grouping strategy
 group_chat:configuration
 metadata:additional metadata
 
"""
    id: str
    workspace: str = ""
    agent_dir: str = ""
    model: str = "gpt-4o"
    tools: ToolPolicy = field(default_factory=ToolPolicy)
    sandbox: SandboxMode = SandboxMode.OFF
    dm_scope: DmScope = DmScope.MAIN
    group_chat: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def default(cls) -> "AgentConfig":
        """createdefaultagent configuration"""
        return cls(id="main")


class BindingRule(BaseModel):
    """

Binding rule
    
    used for convert to agent.
    
"""
    agent_id: str
    peer: Optional[str] = None  # peer ID
    guild_id: Optional[str] = None  # group ID
    team_id: Optional[str] = None  # ID
    account_id: Optional[str] = None  # account ID
    channel: Optional[str] = None  # channel name
    priority: int = 0  # ()
    
    def matches(self, ctx: "RoutingContext") -> bool:
        """check context"""
        if self.peer and self.peer != ctx.peer_id:
            return False
        if self.guild_id and self.guild_id != ctx.guild_id:
            return False
        if self.team_id and self.team_id != ctx.team_id:
            return False
        if self.account_id and self.account_id != ctx.account_id:
            return False
        if self.channel and self.channel != ctx.channel:
            return False
        return True
    
    def specificity(self) -> int:
        """calculate()"""
        score = 0
        if self.peer:
            score += 100
        if self.guild_id:
            score += 50
        if self.team_id:
            score += 30
        if self.account_id:
            score += 20
        if self.channel:
            score += 10
        return score + self.priority


@dataclass
class RoutingContext:
    """

Routing context
    
    contains decision.
    
"""
    peer_id: str
    channel: str
    account_id: str = ""
    guild_id: str = ""
    team_id: str = ""
    chat_type: str = "dm"  # dm / group / thread
    message_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRouter:
    """Route requests to agents using bindings and routing rules.

    Example:
    
        router = AgentRouter()
        
        # Register agents
        router.register_agent(AgentConfig(id="main"))
        router.register_agent(AgentConfig(id="support", model="gpt-4o-mini"))
        
        # Add a binding rule
        router.add_binding(BindingRule(
            agent_id="support",
            channel="telegram",
            priority=10
        ))
        
        #
        ctx = RoutingContext(peer_id="user123", channel="telegram")
        agent = router.route(ctx) # return support agent
    
"""
    
    def __init__(
        self,
        *,
        single_agent_mode: bool = True,
        default_agent_id: str = "main",
    ) -> None:
        """


initialize
 
 Args:
 single_agent_mode:agentmode
 default_agent_id:defaultagent ID
 
"""
        self._agents: dict[str, AgentConfig] = {}
        self._bindings: list[BindingRule] = []
        self._single_agent_mode = single_agent_mode
        self._default_agent_id = default_agent_id
        
        # agentmode registerdefaultagent
        if single_agent_mode:
            self._agents[default_agent_id] = AgentConfig.default()
    
    @property
    def single_agent_mode(self) -> bool:
        """agentmode"""
        return self._single_agent_mode
    
    @property
    def default_agent_id(self) -> str:
        """defaultagent ID"""
        return self._default_agent_id
    
    def register_agent(self, config: AgentConfig) -> None:
        """
registeragent
        
        Args:
            config:agent configuration
        
"""
        self._agents[config.id] = config
        
        # multiagentmode
        if len(self._agents) > 1:
            self._single_agent_mode = False
    
    def unregister_agent(self, agent_id: str) -> bool:
        """

agent
        
        Args:
            agent_id:agent ID
            
        Returns:
            
        
"""
        if agent_id in self._agents:
            del self._agents[agent_id]
            # 
            self._bindings = [b for b in self._bindings if b.agent_id != agent_id]
            return True
        return False
    
    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """
getagent configuration
        
        Args:
            agent_id:agent ID
            
        Returns:
            agent configurationor None
        
"""
        return self._agents.get(agent_id)
    
    def list_agents(self) -> list[AgentConfig]:
        """agent"""
        return list(self._agents.values())
    
    def add_binding(self, rule: BindingRule) -> None:
        """

Binding rule
        
        Args:
            rule:Binding rule
        
"""
        if rule.agent_id not in self._agents:
            raise ValueError(f"Unknown agent: {rule.agent_id}")
        self._bindings.append(rule)
        # (to)
        self._bindings.sort(key=lambda r: -r.specificity())
    
    def remove_binding(self, rule: BindingRule) -> bool:
        """Binding rule"""
        try:
            self._bindings.remove(rule)
            return True
        except ValueError:
            return False
    
    def route(self, ctx: RoutingContext) -> AgentConfig:
        """

to agent
        
        Binding rule, return agent.
        
        Args:
            ctx:Routing context
            
        Returns:
            agent configuration
        
"""
        # agentmode return
        if self._single_agent_mode:
            return self._agents[self._default_agent_id]
        
        # Binding rule
        for rule in self._bindings:
            if rule.matches(ctx):
                agent = self._agents.get(rule.agent_id)
                if agent:
                    return agent
        
        # returndefaultagent
        default = self._agents.get(self._default_agent_id)
        if default:
            return default
        
        # return availableagent
        if self._agents:
            return next(iter(self._agents.values()))
        
        # agent return defaultconfiguration
        return AgentConfig.default()
    
    def get_session_scope(
        self,
        agent: AgentConfig,
        ctx: RoutingContext,
    ) -> str:
        """

based on dm-Scope session
        
        Args:
            agent:agent configuration
            ctx:Routing context
            
        Returns:
            session
        
"""
        scope = agent.dm_scope
        
        if scope == DmScope.MAIN:
            return "main"
        elif scope == DmScope.PER_PEER:
            return f"peer:{ctx.peer_id}"
        elif scope == DmScope.PER_CHANNEL_PEER:
            return f"channel:{ctx.channel}:peer:{ctx.peer_id}"
        elif scope == DmScope.PER_ACCOUNT_CHANNEL_PEER:
            return f"account:{ctx.account_id}:channel:{ctx.channel}:peer:{ctx.peer_id}"
        else:
            return "main"
    
    def check_tool_permission(
        self,
        agent: AgentConfig,
        tool_name: str,
    ) -> bool:
        """

check agent usetool
        
        Args:
            agent:agent configuration
            tool_name:tool name
            
        Returns:
            
        
"""
        return agent.tools.is_allowed(tool_name)


class AgentRouterFactory:
    """


Agent router factory
 
 fromconfigurationcreate instance.
 
"""
    
    @staticmethod
    def from_config(config: dict[str, Any]) -> AgentRouter:
        """


fromconfigurationdictionarycreate
 
 Args:
 config:configurationdictionary
 
 Returns:
 instance
 
"""
        agents_config = config.get("agents", {})
        agent_list = agents_config.get("list", [])
        default_agent_id = agents_config.get("default", "main")
        
        # agentmode
        single_mode = len(agent_list) <= 1
        
        router = AgentRouter(
            single_agent_mode=single_mode,
            default_agent_id=default_agent_id,
        )
        
        # registeragent
        for agent_data in agent_list:
            tools_data = agent_data.get("tools", {})
            tools = ToolPolicy(
                allow=tools_data.get("allow", ["*"]),
                deny=tools_data.get("deny", []),
            )
            
            config_obj = AgentConfig(
                id=agent_data.get("id", "main"),
                workspace=agent_data.get("workspace", ""),
                agent_dir=agent_data.get("agentDir", ""),
                model=agent_data.get("model", "gpt-4o"),
                tools=tools,
                sandbox=SandboxMode(agent_data.get("sandbox", "off")),
                dm_scope=DmScope(agent_data.get("dmScope", "main")),
                group_chat=agent_data.get("groupChat", {}),
                metadata=agent_data.get("metadata", {}),
            )
            router.register_agent(config_obj)
        
        # Binding rule
        bindings = agents_config.get("bindings", [])
        for binding_data in bindings:
            rule = BindingRule(
                agent_id=binding_data.get("agentId", default_agent_id),
                peer=binding_data.get("peer"),
                guild_id=binding_data.get("guildId"),
                team_id=binding_data.get("teamId"),
                account_id=binding_data.get("accountId"),
                channel=binding_data.get("channel"),
                priority=binding_data.get("priority", 0),
            )
            try:
                router.add_binding(rule)
            except ValueError:
                pass  # 
        
        return router
