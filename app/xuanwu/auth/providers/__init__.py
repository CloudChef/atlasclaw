"""Auth providers — unified authentication interface."""

from __future__ import annotations

from app.xuanwu.auth.config import AuthConfig
from app.xuanwu.auth.models import AuthenticationError, AuthResult
from app.xuanwu.auth.providers.base import AuthProvider


# SSO provider types that use external IdP for authentication
# These providers don't need local credential validation - they use OAuth2 flows
_SSO_PROVIDERS = frozenset({"oidc", "dingtalk", "feishu", "wecom"})


class SSOPlaceholderProvider(AuthProvider):
    """
    Placeholder provider for SSO authentication modes (OIDC, DingTalk, etc.).
    
    This provider is used when config.provider is set to an SSO type.
    It allows the middleware to:
      1. Know the correct provider_name for redirect behavior
      2. Continue using Xuanwu JWT tokens for session management
    
    The actual SSO authentication flow is handled by:
      - /api/auth/login -> begin_sso_login() -> external IdP
      - /api/auth/callback -> complete_sso_login() -> issue Xuanwu JWT
    
    This provider's authenticate() always fails because SSO users don't
    present credentials directly - they go through the OAuth2 flow.
    """
    
    def __init__(self, provider_name: str) -> None:
        """
        Args:
            provider_name: The SSO provider type (e.g., "dingtalk", "oidc")
        """
        self._provider_name = provider_name
    
    async def authenticate(self, credential: str) -> AuthResult:
        """
        SSO providers don't authenticate via direct credentials.
        
        Raises:
            AuthenticationError: Always, because SSO uses OAuth2 flow
        """
        raise AuthenticationError(
            f"SSO provider '{self._provider_name}' does not support direct credential "
            f"authentication. Use /api/auth/login to initiate SSO flow."
        )
    
    def provider_name(self) -> str:
        """Return the SSO provider type name."""
        return self._provider_name


def create_local_provider(config: AuthConfig) -> AuthProvider:
    """
    Create the PRIMARY AuthProvider for user authentication.
    
    Primary providers (choose ONE via config.provider):
      - 'none': No authentication (development/single-user mode)
      - 'local': Local username/password authentication (database-backed)
      - 'oidc': OIDC SSO authentication (via external IdP)
      - 'dingtalk': DingTalk SSO authentication
      - 'feishu': Feishu/Lark SSO authentication
      - 'wecom': WeCom/WeChat Work SSO authentication
    
    For SSO providers, this returns a placeholder that:
      - Provides correct provider_name for middleware redirect logic
      - Delegates actual authentication to OAuth2 flow via /api/auth/login
    
    Other authentication mechanisms (oidc_jwt) are always available 
    for specific use cases and DON'T need to be set as primary provider:
      - oidc_jwt: For validating JWT tokens from external API callers
    
    Args:
        config: Auth configuration from xuanwu.json
        
    Returns:
        AuthProvider instance for primary user authentication
        
    Raises:
        ValueError: If provider name is not recognized
        
    Example:
        # Primary auth: local username/password
        config.provider = "local"
        provider = create_provider(config)
        
        # Primary auth: DingTalk SSO
        config.provider = "dingtalk"
        provider = create_provider(config)  # Returns SSOPlaceholderProvider
    """
    provider_type = config.provider.lower()

    if provider_type == "none":
        from app.xuanwu.auth.providers.none import NoneProvider
        return NoneProvider(default_user_id=config.none.default_user_id)

    if provider_type == "local":
        from app.xuanwu.auth.providers.local import LocalAuthProvider
        return LocalAuthProvider()

    # SSO providers use OAuth2 flow, return placeholder for middleware integration
    if provider_type in _SSO_PROVIDERS:
        return SSOPlaceholderProvider(provider_name=provider_type)

    raise ValueError(
        f"Unknown primary auth provider: {config.provider!r}. "
        f"Supported: 'none', 'local', 'oidc', 'dingtalk', 'feishu', 'wecom'. "
        f"Note: 'oidc_jwt' is available via get_jwt_validator() for API use cases."
    )


# ============================================================================
# Secondary providers — always available for specific use cases
# These are NOT set via config.provider, but used directly when needed
# ============================================================================

def get_jwt_validator(
    issuer: str = "",
    client_id: str = "",
    jwks_uri: str = "",
) -> "OIDCJWTProvider":
    """Get OIDC JWT validator for EXTERNAL API authentication.
    
    Use case: External systems calling our API with JWT tokens.
    This is NOT for logging in users to Xuanwu, but for validating
    tokens presented by API clients.
    
    Works without configuration if:
      - Token contains 'iss' claim (issuer auto-discovery)
      - Standard OIDC discovery at {issuer}/.well-known/openid-configuration
    
    Args:
        issuer: Expected token issuer (e.g., "https://keycloak.example.com")
        client_id: Expected audience (optional)
        jwks_uri: JWKS endpoint URL (optional, auto-discovered from issuer)
        
    Returns:
        OIDCJWTProvider configured for token validation
        
    Example:
        # Validate token from request header
        validator = get_jwt_validator()
        try:
            auth_result = await validator.authenticate(token)
        except AuthenticationError:
            raise HTTPException(401, "Invalid token")
    """
    from app.xuanwu.auth.providers.oidc_jwt import OIDCJWTProvider
    return OIDCJWTProvider(
        issuer=issuer,
        client_id=client_id,
        jwks_uri=jwks_uri,
    )


def get_sso_handler(
    issuer: str = "",
    client_id: str = "",
    client_secret: str = "",
    redirect_uri: str = "",
    authorization_endpoint: str = "",
    token_endpoint: str = "",
    userinfo_endpoint: str = "",
    jwks_uri: str = "",
    scopes: list[str] | None = None,
    pkce_enabled: bool = True,
    pkce_method: str = "S256",
    provider_type: str = "oidc",
    # DingTalk-specific parameters
    corp_id: str = "",
    subject_field: str = "unionId",
) -> "OIDCLoginProvider | DingTalkSSOProvider":
    """Get SSO login handler for browser SSO flow.
    
    Use case: Browser-based single sign-on via OAuth2 authorization code flow.
    Handles the complete login flow: generate auth URL → handle callback → 
    exchange code → validate tokens → return user info.
    
    This is separate from primary auth and used when you want to delegate
    authentication to an external IdP (Keycloak, Auth0, Okta, DingTalk, etc.).
    
    Endpoints can be auto-discovered from {issuer}/.well-known/openid-configuration
    if not explicitly provided (OIDC only).
    
    Args:
        issuer: OIDC issuer URL
        client_id: OAuth2 client ID (app_key for DingTalk)
        client_secret: OAuth2 client secret (app_secret for DingTalk)
        redirect_uri: Callback URL after login (e.g., "/auth/callback")
        authorization_endpoint: Auth endpoint (auto-discovered if not set)
        token_endpoint: Token endpoint (auto-discovered if not set)
        userinfo_endpoint: UserInfo endpoint (auto-discovered if not set)
        jwks_uri: JWKS endpoint (auto-discovered if not set)
        scopes: Requested scopes (default: ["openid", "profile", "email"])
        pkce_enabled: Enable PKCE protection (recommended, default: True)
        pkce_method: PKCE method (default: "S256")
        provider_type: Provider type ("oidc" or "dingtalk", default: "oidc")
        corp_id: DingTalk corp ID (only for dingtalk provider)
        subject_field: DingTalk subject field ("unionId" or "openId", default: "unionId")
        
    Returns:
        OIDCLoginProvider or DingTalkSSOProvider configured for SSO flow
        
    Example:
        # OIDC provider
        handler = get_sso_handler(
            issuer="https://keycloak.example.com",
            client_id="xuanwu",
            redirect_uri="https://app.example.com/auth/callback"
        )
        
        # DingTalk provider
        handler = get_sso_handler(
            provider_type="dingtalk",
            client_id="your_app_key",
            client_secret="your_app_secret",
            redirect_uri="https://app.example.com/auth/callback",
            corp_id="your_corp_id",
        )
    """
    if provider_type == "dingtalk":
        from app.xuanwu.auth.providers.dingtalk_sso import DingTalkSSOProvider
        return DingTalkSSOProvider(
            issuer=issuer or "https://login.dingtalk.com",
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes,
            pkce_enabled=pkce_enabled,
            pkce_method=pkce_method,
            corp_id=corp_id,
            subject_field=subject_field,
        )
    
    # Default: OIDC provider
    from app.xuanwu.auth.providers.oidc_login import OIDCLoginProvider
    return OIDCLoginProvider(
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        userinfo_endpoint=userinfo_endpoint,
        jwks_uri=jwks_uri,
        scopes=scopes,
        pkce_enabled=pkce_enabled,
        pkce_method=pkce_method,
    )


__all__ = [
    "create_local_provider",
    "get_jwt_validator",
    "get_sso_handler",
]
