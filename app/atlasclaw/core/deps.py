# Copyright 2021  Qianyun, Inc. All rights reserved.


"""
Per-request dependency container for tools and skills.

`SkillDeps` is the typed payload passed through `RunContext[SkillDeps]`.
It gives tools and skills access to request-scoped metadata such as the
authenticated user identity, peer identity, session key, and optional
extra context.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from app.atlasclaw.auth.models import UserInfo, ANONYMOUS_USER

if TYPE_CHECKING:
    from app.atlasclaw.session.manager import SessionManager


@dataclass(init=False)
class SkillDeps:
    """
    Request-scoped dependencies for ``RunContext[SkillDeps]``.

    Attributes:
        user_info: Authenticated user identity (always present; defaults to
            anonymous when no auth is configured).
        smartcmp_client: Optional SmartCMP HTTP client.
        peer_id: Peer identifier, such as a user or group ID.
        session_key: Stable session key for the current conversation.
        channel: Source channel name for the current request.
        abort_signal: Abort signal shared across the current run.
        session_manager: Optional session manager injected by the caller.
        extra: Additional request-scoped context values.

    Backward compatibility:
        ``deps.user_token`` returns ``deps.user_info.raw_token`` so that
        existing Skills that access the old ``user_token`` attribute continue
        to work without modification.

    Example usage::

        from pydantic_ai import Agent, RunContext
        from app.atlasclaw.core.deps import SkillDeps

        agent = Agent("openai:doubao-pro-32k", deps_type=SkillDeps)

        @agent.tool
        async def query_cloud_entries(ctx: RunContext[SkillDeps], cloud_type: str = None):
            deps = ctx.deps
            headers = {"AtlasClaw-Host-Authenticate": deps.user_token}
            async with httpx.AsyncClient(trust_env=True) as client:

                resp = await client.get("/v1/cloudEntries", headers=headers)
                return resp.json()

        result = await agent.run(
            user_message,
            deps=SkillDeps(
                user_info=UserInfo(user_id="u-abc", raw_token=token),
                peer_id=uid,
                session_key=session_key,
                channel="api",
                abort_signal=asyncio.Event(),
            ),
        )
    """

    user_info: UserInfo = field(default_factory=lambda: ANONYMOUS_USER)
    smartcmp_client: Optional[Any] = None   # httpx.AsyncClient
    peer_id: str = ""
    session_key: str = ""
    channel: str = ""
    abort_signal: asyncio.Event = field(default_factory=asyncio.Event)
    session_manager: Optional[Any] = None   # SessionManager injected by caller (per-user scoped)
    memory_manager: Optional[Any] = None    # MemoryManager injected by caller (per-user scoped)
    extra: dict[str, Any] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)  # All cookies from HTTP request

    def __init__(
        self,
        user_info: Optional[UserInfo] = None,
        *,
        user_token: Optional[str] = None,
        smartcmp_client: Optional[Any] = None,
        peer_id: str = "",
        session_key: str = "",
        channel: str = "",
        abort_signal: Optional[asyncio.Event] = None,
        session_manager: Optional[Any] = None,
        memory_manager: Optional[Any] = None,
        extra: Optional[dict[str, Any]] = None,
        cookies: Optional[dict[str, str]] = None,
    ) -> None:
        resolved_user = user_info or ANONYMOUS_USER
        token = resolved_user.raw_token if user_token is None else str(user_token)
        if resolved_user is ANONYMOUS_USER:
            resolved_user = UserInfo(
                user_id="anonymous",
                display_name=ANONYMOUS_USER.display_name,
                raw_token=token,
            )
        elif token != resolved_user.raw_token:
            resolved_user = UserInfo(
                user_id=resolved_user.user_id,
                display_name=resolved_user.display_name,
                tenant_id=resolved_user.tenant_id,
                roles=list(resolved_user.roles),
                raw_token=token,
                provider_subject=resolved_user.provider_subject,
                extra=dict(resolved_user.extra),
            )

        self.user_info = resolved_user
        self.smartcmp_client = smartcmp_client
        self.peer_id = peer_id
        self.session_key = session_key
        self.channel = channel
        self.abort_signal = abort_signal or asyncio.Event()
        self.session_manager = session_manager
        self.memory_manager = memory_manager
        self.extra = dict(extra or {})
        self.cookies = dict(cookies or {})

    # ------------------------------------------------------------------
    # Backward-compatibility shim
    # ------------------------------------------------------------------

    @property
    def user_token(self) -> str:
        """Alias for ``user_info.raw_token``. Kept for backward compatibility."""
        return self.user_info.raw_token

    # ------------------------------------------------------------------
    # Abort helpers
    # ------------------------------------------------------------------

    def is_aborted(self) -> bool:
        """Return whether the current run has been aborted."""
        return self.abort_signal.is_set()

    def abort(self) -> None:
        """Signal that the current run should stop."""
        self.abort_signal.set()

    def reset_abort(self) -> None:
        """Clear the abort signal."""
        self.abort_signal.clear()
