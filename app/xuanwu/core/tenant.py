# -*- coding: utf-8 -*-
"""



multitenantsupport

implementtenant, configuration managementandcount.
corresponds to tasks.md 6.3.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class TenantConfig(BaseModel):
    """Tenant configuration"""
    id: str
    name: str
    enabled: bool = True
    
    # 
    max_sessions: int = 1000
    max_agents: int = 10
    max_memory_entries: int = 10000
    max_concurrent_runs: int = 50
    
    # feature
    features: dict[str, bool] = Field(default_factory=dict)
    
    # modelconfiguration
    allowed_models: list[str] = Field(default_factory=list)
    default_model: str = "gpt-4o"
    
    # storage
    storage_prefix: str = ""
    
    # metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    def is_model_allowed(self, model: str) -> bool:
        """check model"""
        if not self.allowed_models:
            return True  # 
        return model in self.allowed_models
    
    def is_feature_enabled(self, feature: str) -> bool:
        """check feature"""
        return self.features.get(feature, True)


@dataclass
class TenantUsage:
    """tenant use"""
    tenant_id: str
    session_count: int = 0
    agent_count: int = 0
    memory_entry_count: int = 0
    active_runs: int = 0
    total_tokens_used: int = 0
    total_runs: int = 0
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def check_quota(self, config: TenantConfig) -> tuple[bool, str]:
        """

check
        
        Returns:
            (at, violation reason)
        
"""
        if self.session_count >= config.max_sessions:
            return False, f"Session quota exceeded: {self.session_count}/{config.max_sessions}"
        
        if self.agent_count >= config.max_agents:
            return False, f"Agent quota exceeded: {self.agent_count}/{config.max_agents}"
        
        if self.memory_entry_count >= config.max_memory_entries:
            return False, f"Memory quota exceeded: {self.memory_entry_count}/{config.max_memory_entries}"
        
        if self.active_runs >= config.max_concurrent_runs:
            return False, f"Concurrent run quota exceeded: {self.active_runs}/{config.max_concurrent_runs}"
        
        return True, ""


class TenantIsolation:
    """tenant"""
    
    @staticmethod
    def get_session_prefix(tenant_id: str) -> str:
        """getsessionprefix"""
        return f"tenant:{tenant_id}:"
    
    @staticmethod
    def get_memory_path(tenant_id: str, base_path: str) -> str:
        """get storage"""
        from pathlib import Path
        return str(Path(base_path) / "tenants" / tenant_id / "memory")
    
    @staticmethod
    def get_session_path(tenant_id: str, base_path: str) -> str:
        """get sessionstorage"""
        from pathlib import Path
        return str(Path(base_path) / "tenants" / tenant_id / "sessions")
    
    @staticmethod
    def get_auth_path(tenant_id: str, base_path: str) -> str:
        """get storage"""
        from pathlib import Path
        return str(Path(base_path) / "tenants" / tenant_id / "auth")
    
    @staticmethod
    def isolate_session_key(tenant_id: str, session_key: str) -> str:
        """session key"""
        if session_key.startswith(f"tenant:{tenant_id}:"):
            return session_key
        return f"tenant:{tenant_id}:{session_key}"
    
    @staticmethod
    def extract_tenant_id(session_key: str) -> Optional[str]:
        """fromsession key tenant ID"""
        if session_key.startswith("tenant:"):
            parts = session_key.split(":", 2)
            if len(parts) >= 2:
                return parts[1]
        return None


class TenantManager:
    """

tenantmanager
    
    managemulti-Tenant configuration, use and.
    
    Example usage::
    
        manager = TenantManager()
        
        # registertenant
        await manager.register(TenantConfig(
            id="acme",
            name="Acme Corp",
            max_sessions=500,
        ))
        
        # getTenant configuration
        config = await manager.get("acme")
        
        # check
        ok, reason = await manager.check_quota("acme")
        if not ok:
            raise QuotaExceededError(reason)
        
        # use
        await manager.record_session_created("acme")
    
"""
    
    def __init__(
        self,
        *,
        default_tenant_id: str = "default",
        enable_isolation: bool = True,
    ) -> None:
        """Initialize the tenant manager.

        Args:
            default_tenant_id: Fallback tenant ID used when no tenant is resolved.
            enable_isolation: Whether tenant isolation rules are enforced.
        """
        self._tenants: dict[str, TenantConfig] = {}
        self._usage: dict[str, TenantUsage] = {}
        self._default_tenant_id = default_tenant_id
        self._enable_isolation = enable_isolation
        self._lock = asyncio.Lock()
        
        # registerdefaulttenant
        self._tenants[default_tenant_id] = TenantConfig(
            id=default_tenant_id,
            name="Default Tenant",
        )
        self._usage[default_tenant_id] = TenantUsage(tenant_id=default_tenant_id)
    
    @property
    def default_tenant_id(self) -> str:
        """defaulttenant ID"""
        return self._default_tenant_id
    
    @property
    def isolation_enabled(self) -> bool:
        """"""
        return self._enable_isolation
    
    async def register(self, config: TenantConfig) -> None:
        """
registertenant
        
        Args:
            config:Tenant configuration
        
"""
        async with self._lock:
            self._tenants[config.id] = config
            if config.id not in self._usage:
                self._usage[config.id] = TenantUsage(tenant_id=config.id)
    
    async def unregister(self, tenant_id: str) -> bool:
        """

tenant
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            
        
"""
        if tenant_id == self._default_tenant_id:
            return False  # defaulttenant
        
        async with self._lock:
            if tenant_id in self._tenants:
                del self._tenants[tenant_id]
                if tenant_id in self._usage:
                    del self._usage[tenant_id]
                return True
        return False
    
    async def get(self, tenant_id: str) -> Optional[TenantConfig]:
        """
getTenant configuration
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            Tenant configurationor None
        
"""
        return self._tenants.get(tenant_id)
    
    async def get_or_default(self, tenant_id: Optional[str]) -> TenantConfig:
        """

get-Tenant configuration, at return default
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            Tenant configuration
        
"""
        if tenant_id and tenant_id in self._tenants:
            return self._tenants[tenant_id]
        return self._tenants[self._default_tenant_id]
    
    async def list_tenants(self) -> list[TenantConfig]:
        """tenant"""
        return list(self._tenants.values())
    
    async def get_usage(self, tenant_id: str) -> Optional[TenantUsage]:
        """

get tenantuse
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            use or None
        
"""
        return self._usage.get(tenant_id)
    
    async def check_quota(self, tenant_id: str) -> tuple[bool, str]:
        """

check tenant
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            (at, violation reason)
        
"""
        config = self._tenants.get(tenant_id)
        usage = self._usage.get(tenant_id)
        
        if not config:
            return False, f"Unknown tenant: {tenant_id}"
        
        if not config.enabled:
            return False, f"Tenant disabled: {tenant_id}"
        
        if not usage:
            return True, ""
        
        return usage.check_quota(config)
    
    async def record_session_created(self, tenant_id: str) -> bool:
        """

sessioncreate
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            at
        
"""
        async with self._lock:
            usage = self._usage.get(tenant_id)
            if usage:
                usage.session_count += 1
                usage.last_activity = datetime.now(timezone.utc)
                config = self._tenants.get(tenant_id)
                if config:
                    return usage.session_count <= config.max_sessions
        return True
    
    async def record_session_deleted(self, tenant_id: str) -> None:
        """

session
        
        Args:
            tenant_id:tenant ID
        
"""
        async with self._lock:
            usage = self._usage.get(tenant_id)
            if usage and usage.session_count > 0:
                usage.session_count -= 1
    
    async def record_run_started(self, tenant_id: str) -> bool:
        """

runstart
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            at
        
"""
        async with self._lock:
            usage = self._usage.get(tenant_id)
            if usage:
                usage.active_runs += 1
                usage.total_runs += 1
                usage.last_activity = datetime.now(timezone.utc)
                config = self._tenants.get(tenant_id)
                if config:
                    return usage.active_runs <= config.max_concurrent_runs
        return True
    
    async def record_run_completed(self, tenant_id: str, tokens_used: int = 0) -> None:
        """

run
        
        Args:
            tenant_id:tenant ID
            tokens_used:use token count
        
"""
        async with self._lock:
            usage = self._usage.get(tenant_id)
            if usage:
                if usage.active_runs > 0:
                    usage.active_runs -= 1
                usage.total_tokens_used += tokens_used
    
    async def record_memory_created(self, tenant_id: str) -> bool:
        """

create
        
        Args:
            tenant_id:tenant ID
            
        Returns:
            at
        
"""
        async with self._lock:
            usage = self._usage.get(tenant_id)
            if usage:
                usage.memory_entry_count += 1
                config = self._tenants.get(tenant_id)
                if config:
                    return usage.memory_entry_count <= config.max_memory_entries
        return True
    
    def resolve_tenant(
        self,
        session_key: Optional[str] = None,
        user_id: Optional[str] = None,
        request_headers: Optional[dict[str, str]] = None,
    ) -> str:
        """

parsetenant ID
        
        :session key > > default
        
        Args:
            session_key:session key
            user_id:user ID
            request_headers:
            
        Returns:
            tenant ID
        
"""
        # fromsession key
        if session_key:
            tenant_id = TenantIsolation.extract_tenant_id(session_key)
            if tenant_id and tenant_id in self._tenants:
                return tenant_id
        
        # from
        if request_headers:
            tenant_id = request_headers.get("X-Tenant-ID") or request_headers.get("x-tenant-id")
            if tenant_id and tenant_id in self._tenants:
                return tenant_id
        
        return self._default_tenant_id
    
    def isolate_session_key(self, tenant_id: str, session_key: str) -> str:
        """

session key
        
        Args:
            tenant_id:tenant ID
            session_key:rawsession key
            
        Returns:
            session key
        
"""
        if not self._enable_isolation:
            return session_key
        return TenantIsolation.isolate_session_key(tenant_id, session_key)
