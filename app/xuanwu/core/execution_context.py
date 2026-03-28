# -*- coding: utf-8 -*-
"""


Execution context

implement-Sandbox mode, and-Execution contextmanage.
corresponds to tasks.md 6.1.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel, Field


class SandboxMode(Enum):
    """Sandbox mode"""
    OFF = "off"  # 
    AGENT = "agent"  # agent()
    SESSION = "session"  # session()


class ResourceLimit(BaseModel):
    """"""
    max_memory_mb: int = 512  # MB
    max_cpu_percent: float = 50.0  # CPU
    max_file_size_mb: int = 100  # MB
    max_files: int = 1000  # count
    max_network_connections: int = 100  # connectioncount
    timeout_seconds: int = 600  # executetimeout in seconds


class FileAccessPolicy(BaseModel):
    """"""
    allow_read: list[str] = Field(default_factory=list)  # mode
    allow_write: list[str] = Field(default_factory=list)  # mode
    deny: list[str] = Field(default_factory=list)  # mode
    
    def can_read(self, path: str) -> bool:
        """check"""
        # 
        for pattern in self.deny:
            if self._match_pattern(pattern, path):
                return False
        
        # such as, default
        if not self.allow_read:
            return True
        
        for pattern in self.allow_read:
            if self._match_pattern(pattern, path):
                return True
        return False
    
    def can_write(self, path: str) -> bool:
        """check"""
        # 
        for pattern in self.deny:
            if self._match_pattern(pattern, path):
                return False
        
        # such as, default
        if not self.allow_write:
            return False
        
        for pattern in self.allow_write:
            if self._match_pattern(pattern, path):
                return True
        return False
    
    def _match_pattern(self, pattern: str, path: str) -> bool:
        """mode"""
        from fnmatch import fnmatch
        return fnmatch(path, pattern) or path.startswith(pattern.rstrip("*"))


class NetworkAccessPolicy(BaseModel):
    """"""
    allow_hosts: list[str] = Field(default_factory=lambda: ["*"])  # 
    deny_hosts: list[str] = Field(default_factory=list)  # 
    allow_ports: list[int] = Field(default_factory=list)  # 
    deny_ports: list[int] = Field(default_factory=lambda: [22, 3389])  # 
    
    def can_connect(self, host: str, port: int) -> bool:
        """check connection"""
        # check
        if port in self.deny_ports:
            return False
        if self.allow_ports and port not in self.allow_ports:
            return False
        
        # check
        for pattern in self.deny_hosts:
            if self._match_host(pattern, host):
                return False
        
        if "*" in self.allow_hosts:
            return True
        
        for pattern in self.allow_hosts:
            if self._match_host(pattern, host):
                return True
        return False
    
    def _match_host(self, pattern: str, host: str) -> bool:
        """mode"""
        if pattern == "*":
            return True
        if pattern.startswith("*."):
            return host.endswith(pattern[1:])
        return pattern == host


class SecurityPolicy(BaseModel):
    """


    
    , and.
    
"""
    file_access: FileAccessPolicy = Field(default_factory=FileAccessPolicy)
    network_access: NetworkAccessPolicy = Field(default_factory=NetworkAccessPolicy)
    resource_limit: ResourceLimit = Field(default_factory=ResourceLimit)
    
    # tool
    tools_allow: list[str] = Field(default_factory=lambda: ["*"])
    tools_deny: list[str] = Field(default_factory=list)
    
    # environment variable
    allowed_env_vars: list[str] = Field(default_factory=list)
    hidden_env_vars: list[str] = Field(
        default_factory=lambda: ["*KEY*", "*SECRET*", "*TOKEN*", "*PASSWORD*"]
    )
    
    def is_tool_allowed(self, tool_name: str) -> bool:
        """check tool"""
        from fnmatch import fnmatch
        
        # 
        for pattern in self.tools_deny:
            if fnmatch(tool_name, pattern):
                return False
        
        # check
        for pattern in self.tools_allow:
            if fnmatch(tool_name, pattern):
                return True
        
        return False
    
    def filter_env_vars(self, env: dict[str, str]) -> dict[str, str]:
        """filterenvironment variable"""
        from fnmatch import fnmatch
        
        filtered = {}
        for key, value in env.items():
            # check
            hidden = any(fnmatch(key, p) for p in self.hidden_env_vars)
            if hidden:
                continue
            
            # such as list,
            if self.allowed_env_vars:
                if any(fnmatch(key, p) for p in self.allowed_env_vars):
                    filtered[key] = value
            else:
                filtered[key] = value
        
        return filtered
    
    @classmethod
    def permissive(cls) -> "SecurityPolicy":
        """create"""
        return cls(
            file_access=FileAccessPolicy(allow_read=["*"], allow_write=["*"]),
            network_access=NetworkAccessPolicy(allow_hosts=["*"]),
            tools_allow=["*"],
        )
    
    @classmethod
    def restrictive(cls) -> "SecurityPolicy":
        """create"""
        return cls(
            file_access=FileAccessPolicy(
                allow_read=["./", "./*", "./**/*"],
                allow_write=[],
                deny=["../*", "/etc/*", "/root/*", "C:\\Windows\\*"],
            ),
            network_access=NetworkAccessPolicy(
                allow_hosts=[],
                deny_hosts=["*"],
            ),
            tools_allow=["read_file", "list_files", "search"],
            tools_deny=["execute", "shell", "bash", "write_file"],
            resource_limit=ResourceLimit(
                max_memory_mb=256,
                max_cpu_percent=25.0,
                timeout_seconds=300,
            ),
        )


@dataclass
class ExecutionContext:
    """

Execution context
    
    execute context and.
    
    Example usage::
    
        ctx = ExecutionContext(
            agent_id="main",
            session_key="agent:main:api:dm:user123",
            sandbox_mode=SandboxMode.AGENT,
        )
        
        # check
        if ctx.can_use_tool("bash"):
            # executetool
            pass
        
        # check
        if ctx.can_read_file("/path/to/file"):
            # read file
            pass
    
"""
    agent_id: str
    session_key: str
    sandbox_mode: SandboxMode = SandboxMode.OFF
    security_policy: SecurityPolicy = field(default_factory=SecurityPolicy)
    
    # runtime information
    workspace: str = ""
    working_dir: str = ""
    user_id: str = ""
    tenant_id: str = ""
    
    # 
    started_at: float = 0.0
    timeout_at: float = 0.0
    is_aborted: bool = False
    
    # use
    memory_used_mb: float = 0.0
    cpu_time_seconds: float = 0.0
    files_created: int = 0
    network_connections: int = 0
    
    # additional data
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.started_at:
            import time
            self.started_at = time.monotonic()
        if not self.timeout_at:
            self.timeout_at = self.started_at + self.security_policy.resource_limit.timeout_seconds
    
    def can_use_tool(self, tool_name: str) -> bool:
        """check usetool"""
        if self.is_aborted:
            return False
        return self.security_policy.is_tool_allowed(tool_name)
    
    def can_read_file(self, path: str) -> bool:
        """check read file"""
        if self.is_aborted:
            return False
        return self.security_policy.file_access.can_read(path)
    
    def can_write_file(self, path: str) -> bool:
        """check"""
        if self.is_aborted:
            return False
        if self.sandbox_mode == SandboxMode.OFF:
            return self.security_policy.file_access.can_write(path)
        
        # Sandbox mode workspace
        if self.workspace:
            resolved = str(Path(path).resolve())
            workspace_resolved = str(Path(self.workspace).resolve())
            if not resolved.startswith(workspace_resolved):
                return False
        
        return self.security_policy.file_access.can_write(path)
    
    def can_connect(self, host: str, port: int) -> bool:
        """check connection"""
        if self.is_aborted:
            return False
        if self.network_connections >= self.security_policy.resource_limit.max_network_connections:
            return False
        return self.security_policy.network_access.can_connect(host, port)
    
    def check_timeout(self) -> bool:
        """check"""
        import time
        return time.monotonic() > self.timeout_at
    
    def check_resources(self) -> tuple[bool, str]:
        """

check
        
        Returns:
            (whether it is within the limit, violation reason)
        
"""
        limits = self.security_policy.resource_limit
        
        if self.memory_used_mb > limits.max_memory_mb:
            return False, f"Memory limit exceeded: {self.memory_used_mb:.1f}MB > {limits.max_memory_mb}MB"
        
        if self.files_created > limits.max_files:
            return False, f"File limit exceeded: {self.files_created} > {limits.max_files}"
        
        if self.check_timeout():
            return False, "Execution timeout"
        
        return True, ""
    
    def abort(self) -> None:
        """in execute"""
        self.is_aborted = True
    
    def record_file_created(self) -> bool:
        """

create
        
        Returns:
            whether it is within the limit
        
"""
        self.files_created += 1
        return self.files_created <= self.security_policy.resource_limit.max_files
    
    def record_connection(self) -> bool:
        """

connection
        
        Returns:
            whether it is within the limit
        
"""
        self.network_connections += 1
        return self.network_connections <= self.security_policy.resource_limit.max_network_connections
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary"""
        return {
            "agent_id": self.agent_id,
            "session_key": self.session_key,
            "sandbox_mode": self.sandbox_mode.value,
            "workspace": self.workspace,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "is_aborted": self.is_aborted,
            "memory_used_mb": self.memory_used_mb,
            "files_created": self.files_created,
            "network_connections": self.network_connections,
        }


class ExecutionContextManager:
    """

Execution contextmanager
    
    management-Execution context.
    
"""
    
    def __init__(self) -> None:
        self._contexts: dict[str, ExecutionContext] = {}
        self._lock = asyncio.Lock()
    
    async def create(
        self,
        agent_id: str,
        session_key: str,
        *,
        sandbox_mode: SandboxMode = SandboxMode.OFF,
        security_policy: Optional[SecurityPolicy] = None,
        workspace: str = "",
        user_id: str = "",
        tenant_id: str = "",
    ) -> ExecutionContext:
        """

createExecution context
        
        Args:
            agent_id:agent ID
            session_key:session key
            sandbox_mode:Sandbox mode
            security_policy:
            workspace:workspace path
            user_id:user ID
            tenant_id:tenant ID
            
        Returns:
            Execution context
        
"""
        ctx = ExecutionContext(
            agent_id=agent_id,
            session_key=session_key,
            sandbox_mode=sandbox_mode,
            security_policy=security_policy or SecurityPolicy(),
            workspace=workspace,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        
        async with self._lock:
            self._contexts[session_key] = ctx
        
        return ctx
    
    async def get(self, session_key: str) -> Optional[ExecutionContext]:
        """getExecution context"""
        return self._contexts.get(session_key)
    
    async def remove(self, session_key: str) -> bool:
        """Execution context"""
        async with self._lock:
            if session_key in self._contexts:
                del self._contexts[session_key]
                return True
        return False
    
    async def cleanup_expired(self) -> int:
        """

context
        
        Returns:
            context data
        
"""
        import time
        now = time.monotonic()
        
        expired = []
        for key, ctx in self._contexts.items():
            if ctx.check_timeout():
                expired.append(key)
        
        async with self._lock:
            for key in expired:
                if key in self._contexts:
                    del self._contexts[key]
        
        return len(expired)
