"""Auth providers — unified authentication interface."""

from __future__ import annotations

from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.providers.base import AuthProvider


def create_local_provider(config: AuthConfig) -> AuthProvider:
    """
    Create the PRIMARY AuthProvider for user authentication.
    
    Primary providers (choose ONE via config.provider):
      - 'none': No authentication (development/single-user mode)
      - 'local': Local username/password authentication (database-backed)
    
    Other authentication mechanisms (oidc_jwt, oidc_login) are always available 
    for specific use cases and DON'T need to be set as primary provider:
      - oidc_jwt: For validating JWT tokens from external API callers
      - oidc_login: For handling browser SSO login flows
    
    Args:
        config: Auth configuration from atlasclaw.json
        
    Returns:
        AuthProvider instance for primary user authentication
        
    Raises:
        ValueError: If provider name is not recognized
        
    Example:
        # Primary auth: local username/password
        config.provider = "local"
        provider = create_provider(config)
        
        # For API JWT validation (separate from primary auth)
        jwt_validator = get_jwt_validator(issuer="https://idp.example.com")
    """
    provider_type = config.provider.lower()

    if provider_type == "none":
        from app.atlasclaw.auth.providers.none import NoneProvider
        return NoneProvider(default_user_id=config.none.default_user_id)

    if provider_type == "local":
        from app.atlasclaw.auth.providers.local import LocalAuthProvider
        return LocalAuthProvider()

    raise ValueError(
        f"Unknown primary auth provider: {config.provider!r}. "
        f"Supported: 'none', 'local'. "
        f"Note: 'oidc_jwt' and 'oidc_login' are available via get_jwt_validator() "
        f"and get_sso_handler() for API/SSO use cases."
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
    This is NOT for logging in users to AtlasClaw, but for validating
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
    from app.atlasclaw.auth.providers.oidc_jwt import OIDCJWTProvider
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
) -> "OIDCLoginProvider":
    """Get OIDC login handler for browser SSO flow.
    
    Use case: Browser-based single sign-on via OAuth2 authorization code flow.
    Handles the complete login flow: generate auth URL → handle callback → 
    exchange code → validate tokens → return user info.
    
    This is separate from primary auth and used when you want to delegate
    authentication to an external IdP (Keycloak, Auth0, Okta, etc.).
    
    Endpoints can be auto-discovered from {issuer}/.well-known/openid-configuration
    if not explicitly provided.
    
    Args:
        issuer: OIDC issuer URL
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret (for confidential clients)
        redirect_uri: Callback URL after login (e.g., "/auth/callback")
        authorization_endpoint: Auth endpoint (auto-discovered if not set)
        token_endpoint: Token endpoint (auto-discovered if not set)
        userinfo_endpoint: UserInfo endpoint (auto-discovered if not set)
        jwks_uri: JWKS endpoint (auto-discovered if not set)
        scopes: Requested scopes (default: ["openid", "profile", "email"])
        pkce_enabled: Enable PKCE protection (recommended, default: True)
        pkce_method: PKCE method (default: "S256")
        
    Returns:
        OIDCLoginProvider configured for SSO flow
        
    Example:
        # Step 1: Generate login URL
        handler = get_sso_handler(
            issuer="https://keycloak.example.com",
            client_id="atlasclaw",
            redirect_uri="https://app.example.com/auth/callback"
        )
        login_url = handler.build_authorization_url(state="random-state")
        
        # Step 2: Handle callback
        auth_result = await handler.complete_login(code, code_verifier)
    """
    from app.atlasclaw.auth.providers.oidc_login import OIDCLoginProvider
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
