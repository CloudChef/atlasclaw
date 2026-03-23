"""Provider factory — creates the correct AuthProvider from AuthConfig."""

from __future__ import annotations

from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.providers.base import AuthProvider


def create_provider(config: AuthConfig) -> AuthProvider:
    """
    Instantiate the AuthProvider specified by ``config.provider``.

    Raises:
        ValueError: if the provider name is not recognised.
    """
    provider_type = config.provider.lower()

    if provider_type == "none":
        from app.atlasclaw.auth.providers.none import NoneProvider
        return NoneProvider(default_user_id=config.none.default_user_id)

    if provider_type == "smartcmp":
        from app.atlasclaw.auth.providers.smartcmp import SmartCMPProvider
        sc = config.smartcmp.expanded()
        return SmartCMPProvider(
            validate_url=sc.validate_url,
            api_base_url=sc.api_base_url,
        )

    if provider_type == "oidc":
        from app.atlasclaw.auth.providers.oidc import OIDCProvider
        oidc = config.oidc.expanded()
        return OIDCProvider(
            issuer=oidc.issuer,
            client_id=oidc.client_id,
            jwks_uri=oidc.jwks_uri,
        )

    if provider_type == "api_key":
        from app.atlasclaw.auth.providers.api_key import APIKeyProvider
        return APIKeyProvider(keys=config.api_key.keys)

    if provider_type == "local":
        from app.atlasclaw.auth.providers.local import LocalAuthProvider
        return LocalAuthProvider()

    # Built-in DingTalk OIDC provider (uses standard OIDCProvider with DingTalk config)
    if provider_type == "dingtalk_oidc":
        from app.atlasclaw.auth.providers.oidc import OIDCProvider
        dt = config.dingtalk_oidc.expanded()
        return OIDCProvider(
            issuer=dt.issuer,
            client_id=dt.client_id,
            jwks_uri=dt.jwks_uri,
        )

    # --- Extension providers registered via AuthRegistry (e.g. from providers_root) ---
    from app.atlasclaw.auth.registry import AuthRegistry
    provider_class = AuthRegistry.get(provider_type)
    if provider_class is not None:
        # Generic fallback for other extension providers
        return provider_class()

    raise ValueError(
        f"Unknown auth provider: {config.provider!r}. "
        "Supported values: 'none', 'smartcmp', 'oidc', 'api_key', 'local', 'dingtalk_oidc', "
        "plus any registered extension providers."
    )



__all__ = ["create_provider"]
