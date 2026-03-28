"""NoneProvider — no-auth / development mode."""

from __future__ import annotations

from app.xuanwu.auth.models import AuthResult
from app.xuanwu.auth.providers.base import AuthProvider


class NoneProvider(AuthProvider):
    """
    Pass-through provider for development or single-user deployments.
    Always returns a fixed AuthResult with the configured default_user_id.
    """

    def __init__(self, default_user_id: str = "default") -> None:
        self._default_user_id = default_user_id

    def provider_name(self) -> str:
        return "none"

    async def authenticate(self, credential: str) -> AuthResult:
        return AuthResult(
            subject=self._default_user_id,
            display_name="Default User",
        )
