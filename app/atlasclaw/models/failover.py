# Copyright 2021  Qianyun, Inc. All rights reserved.


"""

model

implement-Authentication profile andmodel:
- Authentication profilestorage:support api_key, oauth, accesskey credential type
-:OAuth higher priority than API Key, typein
- session:session profile
-:exponential backoff 1min -> 5min -> 25min -> 1h()
-:from 5h start,, 24h
-:provider profile, to fallbacks model
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Any


class CredentialType(str, Enum):
    """credential type"""
    API_KEY = "api_key"
    OAUTH = "oauth"
    ACCESS_KEY = "accesskey"


@dataclass
class AuthProfile:
    """

Authentication profile
    
    Attributes:
        profile_id:profile ID(format:provider:default or provider:email)
        provider:providername
        credential_type:credential type
        credential:
        cooldown_until:
        cooldown_count:count(used for exponential backoff)
        disabled_until:()
        created_at:creation time
    
"""
    profile_id: str
    provider: str
    credential_type: CredentialType
    credential: str
    cooldown_until: Optional[datetime] = None
    cooldown_count: int = 0
    disabled_until: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    
    def is_available(self) -> bool:
        """check profile available"""
        now = datetime.now()
        
        if self.disabled_until and now < self.disabled_until:
            return False
        
        if self.cooldown_until and now < self.cooldown_until:
            return False
        
        return True


@dataclass
class ModelFailoverConfig:
    """

model configuration
    
    Attributes:
        primary_model:model
        fallbacks:modellist
        cooldown_sequence:()
        billing_initial_cooldown:()
        billing_max_cooldown:()
    
"""
    primary_model: str = "doubao-pro-32k"
    fallbacks: list[str] = field(default_factory=list)
    cooldown_sequence: list[int] = field(
        default_factory=lambda: [60, 300, 1500, 3600]  # 1min, 5min, 25min, 1h
    )
    billing_initial_cooldown: int = 18000  # 5h
    billing_max_cooldown: int = 86400  # 24h


class ModelFailover:
    """

model manager
    
    managemulti Authentication profile, and.
    
    Example usage:
        ```python
        failover = ModelFailover(profiles, config)
        
        # get currently availablemodeland
        model, headers = await failover.get_client(session_key)
        
        #
        failover.report_failure(profile_id, "rate_limit")
        ```
    
"""
    
    def __init__(
        self,
        profiles: list[AuthProfile],
        config: ModelFailoverConfig,
    ):
        """


initialize manager
 
 Args:
 profiles:Authentication profilelist
 config:configuration
 
"""
        self.profiles = {p.profile_id: p for p in profiles}
        self.config = config
        
        # session
        self._session_sticky: dict[str, str] = {}
        
        # model(used for)
        self._current_model_index = 0
    
    @property
    def current_model(self) -> str:
        """get model"""
        all_models = [self.config.primary_model] + self.config.fallbacks
        return all_models[self._current_model_index % len(all_models)]
    
    async def get_client(
        self,
        session_key: str,
    ) -> tuple[str, dict]:
        """

get currently availablemodeland
        
        Args:
            session_key:session key
            
        Returns:
            (modelname, dictionary)
        
"""
        # 1. session:use profile
        if session_key in self._session_sticky:
            profile = self._get_profile(self._session_sticky[session_key])
            if profile and profile.is_available():
                return self.current_model, self._make_headers(profile)
            else:
                # profile available,
                del self._session_sticky[session_key]
        
        # 2. available profile
        sorted_profiles = self._sorted_profiles()
        for profile in sorted_profiles:
            if profile.is_available():
                # to session
                self._session_sticky[session_key] = profile.profile_id
                return self.current_model, self._make_headers(profile)
        
        # 3. profile available, model
        if self._try_fallback():
            # model profile
            return await self.get_client(session_key)
        
        raise RuntimeError("All models and profiles exhausted")
    
    def report_failure(
        self,
        profile_id: str,
        error_type: str,
    ) -> None:
        """


        
        Args:
            profile_id:profile ID
            error_type:type(rate_limit, auth, timeout, billing, for mat)
        
"""
        profile = self._get_profile(profile_id)
        if not profile:
            return
        
        if error_type == "billing":
            # :use
            cooldown_secs = min(
                self.config.billing_initial_cooldown * (2 ** profile.cooldown_count),
                self.config.billing_max_cooldown,
            )
            profile.disabled_until = datetime.now() + timedelta(seconds=cooldown_secs)
        elif error_type in ("rate_limit", "auth", "timeout"):
            # :exponential backoff
            idx = min(profile.cooldown_count, len(self.config.cooldown_sequence) - 1)
            cooldown_secs = self.config.cooldown_sequence[idx]
            profile.cooldown_until = datetime.now() + timedelta(seconds=cooldown_secs)
            profile.cooldown_count += 1
        # for mat trigger,
    
    def report_success(self, profile_id: str) -> None:
        """


        
        Args:
            profile_id:profile ID
        
"""
        profile = self._get_profile(profile_id)
        if profile:
            # count
            profile.cooldown_count = 0
            profile.cooldown_until = None
    
    def reset_session_sticky(self, session_key: str) -> None:
        """

Reset a session
        
        Args:
            session_key:session key
        
"""
        self._session_sticky.pop(session_key, None)
    
    def add_profile(self, profile: AuthProfile) -> None:
        """

Authentication profile
        
        Args:
            profile:Authentication profile
        
"""
        self.profiles[profile.profile_id] = profile
    
    def remove_profile(self, profile_id: str) -> bool:
        """

Authentication profile
        
        Args:
            profile_id:profile ID
            
        Returns:
            
        
"""
        if profile_id in self.profiles:
            del self.profiles[profile_id]
            return True
        return False
    
    def _get_profile(self, profile_id: str) -> Optional[AuthProfile]:
        """getprofile"""
        return self.profiles.get(profile_id)
    
    def _sorted_profiles(self) -> list[AuthProfile]:
        """

profile
        
        Rules:
        1. OAuth higher priority than API Key
        2. typein,(created_at)
        3. / to
        
"""
        def sort_key(p: AuthProfile) -> tuple:
            # available(available)
            available = 0 if p.is_available() else 1
            # credential type(OAuth)
            type_order = 0 if p.credential_type == CredentialType.OAUTH else 1
            # creation time()
            return (available, type_order, p.created_at)
        
        return sorted(self.profiles.values(), key=sort_key)
    
    def _make_headers(self, profile: AuthProfile) -> dict:
        """


        
        Args:
            profile:Authentication profile
            
        Returns:
            dictionary
        
"""
        if profile.credential_type == CredentialType.API_KEY:
            return {"Authorization": f"Bearer {profile.credential}"}
        elif profile.credential_type == CredentialType.OAUTH:
            return {"Authorization": f"Bearer {profile.credential}"}
        elif profile.credential_type == CredentialType.ACCESS_KEY:
            return {"AtlasClaw-Host-Authenticate": profile.credential}
        return {}
    
    def _try_fallback(self) -> bool:
        """

to model
        
        Returns:
            (available model)
        
"""
        all_models = [self.config.primary_model] + self.config.fallbacks
        if self._current_model_index < len(all_models) - 1:
            self._current_model_index += 1
            return True
        return False
    
    def reset_fallback(self) -> None:
        """(to model)"""
        self._current_model_index = 0
    
    def get_status(self) -> dict:
        """

get
        
        Returns:
            dictionary
        
"""
        available = sum(1 for p in self.profiles.values() if p.is_available())
        return {
            "current_model": self.current_model,
            "total_profiles": len(self.profiles),
            "available_profiles": available,
            "session_sticky_count": len(self._session_sticky),
        }
