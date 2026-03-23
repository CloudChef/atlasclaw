# -*- coding: utf-8 -*-
"""DingTalk OAuth2 SSO login flow (non-standard, not OIDC)."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.atlasclaw.auth.models import AuthResult, AuthenticationError
from app.atlasclaw.auth.providers.oidc_sso import OIDCSSOProvider

logger = logging.getLogger(__name__)

# DingTalk OAuth2 endpoints (non-standard OIDC)
DINGTALK_AUTHORIZATION_ENDPOINT = "https://login.dingtalk.com/oauth2/auth"
DINGTALK_TOKEN_ENDPOINT = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
DINGTALK_USERINFO_ENDPOINT = "https://api.dingtalk.com/v1.0/contact/users/me"


class DingTalkSSOProvider(OIDCSSOProvider):
    """
    DingTalk OAuth2 SSO login flow.

    钉钉使用自定义 OAuth2 流程，与标准 OIDC 有以下差异:
      - Token 端点使用 JSON 请求体 + camelCase 参数（不是 form-encoded）
      - 不返回 id_token，只返回 accessToken + refreshToken
      - 用户信息端点使用自定义 header `x-acs-dingtalk-access-token`（不是 Bearer）
      - 用户标识从 userinfo 的 `unionId`/`openId` 获取（不是 id_token 的 `sub`）
      - 无 OIDC Discovery 端点

    Attributes:
        corp_id: 钉钉企业 corpId，用于映射 tenant_id。
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str = "",
        redirect_uri: str = "",
        authorization_endpoint: str = "",
        token_endpoint: str = "",
        userinfo_endpoint: str = "",
        scopes: Optional[list[str]] = None,
        pkce_enabled: bool = True,
        pkce_method: str = "S256",
        corp_id: str = "",
    ) -> None:
        """
        Initialize DingTalk SSO Provider.

        Args:
            issuer: Issuer URL (用于基类兼容，钉钉不使用)。
            client_id: 钉钉应用的 AppKey。
            client_secret: 钉钉应用的 AppSecret。
            redirect_uri: OAuth2 回调 URI。
            authorization_endpoint: 授权端点，默认 DingTalk 官方端点。
            token_endpoint: Token 交换端点，默认 DingTalk 官方端点。
            userinfo_endpoint: 用户信息端点，默认 DingTalk 官方端点。
            scopes: OAuth2 scopes，默认 ["openid", "corpid"]。
            pkce_enabled: 是否启用 PKCE，默认 True。
            pkce_method: PKCE 方法，默认 "S256"。
            corp_id: 钉钉企业 corpId，用于映射 tenant_id。
        """
        # Use DingTalk default endpoints if not explicitly configured
        super().__init__(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            authorization_endpoint=authorization_endpoint or DINGTALK_AUTHORIZATION_ENDPOINT,
            token_endpoint=token_endpoint or DINGTALK_TOKEN_ENDPOINT,
            userinfo_endpoint=userinfo_endpoint or DINGTALK_USERINFO_ENDPOINT,
            jwks_uri="",  # DingTalk does not use JWKS
            scopes=scopes or ["openid", "corpid"],
            pkce_enabled=pkce_enabled,
            pkce_method=pkce_method,
        )
        self._corp_id = corp_id

    def build_authorization_url(self, state: str, code_challenge: str = "") -> str:
        """
        Build DingTalk authorization URL with required prompt=consent.

        钉钉要求授权 URL 必须包含 prompt=consent 参数，
        用户才会进入授权确认页，显式同意 Contact.User.Read 等权限。
        否则 token 不会包含这些权限，导致 userinfo 接口返回 403。

        See: https://open-dingtalk.github.io/developerpedia/docs/develop/permission/token/browser/get_user_app_token_browser/
        """
        base_url = super().build_authorization_url(state, code_challenge)
        # DingTalk requires prompt=consent to obtain user authorization scope
        return f"{base_url}&prompt=consent"

    async def exchange_code(self, code: str, code_verifier: str = "") -> dict[str, Any]:
        """
        Exchange authorization code for tokens (DingTalk custom format).

        钉钉使用 JSON 请求体而非 form-encoded：
        - 参数使用 camelCase: clientId, clientSecret, code, grantType
        - 不使用 HTTP Basic Auth

        Args:
            code: 授权码。
            code_verifier: PKCE code_verifier（如果启用）。

        Returns:
            Token 响应 JSON，包含 accessToken, refreshToken, expireIn。

        Raises:
            AuthenticationError: Token 交换失败时。
        """
        # DingTalk uses JSON request body with camelCase parameters
        payload: dict[str, str] = {
            "clientId": self._client_id,
            "clientSecret": self._client_secret,
            "code": code,
            "grantType": "authorization_code",
        }

        logger.info(
            "[DingTalk SSO] Exchanging code at %s",
            self._token_endpoint,
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self._token_endpoint,
                    json=payload,  # JSON request body, not form data
                    headers={"Content-Type": "application/json"},
                )
                logger.debug(
                    "[DingTalk SSO] Token exchange response: status=%s body=%s",
                    resp.status_code,
                    resp.text[:500] if resp.text else "",
                )
                resp.raise_for_status()
                token_data = resp.json()
                # Log first 10 chars of accessToken for debugging (not full token)
                at = token_data.get("accessToken", "")
                logger.info(
                    "[DingTalk SSO] Token exchange success, accessToken prefix: %s...",
                    at[:10] if at else "<empty>",
                )
                return token_data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[DingTalk SSO] Token exchange failed: status=%s body=%s",
                exc.response.status_code,
                exc.response.text[:500] if exc.response.text else "",
            )
            raise AuthenticationError(
                f"DingTalk token exchange failed: {exc.response.status_code}"
            )
        except Exception as exc:
            logger.error("[DingTalk SSO] Token exchange error: %s", exc)
            raise AuthenticationError(f"DingTalk token exchange failed: {exc}")

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """
        Fetch user info from DingTalk (custom header format).

        钉钉使用自定义 header `x-acs-dingtalk-access-token`，不是标准 Bearer。

        Args:
            access_token: DingTalk access token。

        Returns:
            用户信息 JSON，包含 nick, unionId, openId, mobile, email, avatarUrl 等。

        Raises:
            AuthenticationError: 获取用户信息失败时（网络错误或 API 返回错误）。
        """
        logger.info(
            "[DingTalk SSO] Fetching userinfo from %s",
            self._userinfo_endpoint,
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    self._userinfo_endpoint,
                    headers={
                        # DingTalk uses custom header, not Authorization: Bearer
                        "x-acs-dingtalk-access-token": access_token,
                    },
                )
                logger.debug(
                    "[DingTalk SSO] Userinfo response: status=%s body=%s",
                    resp.status_code,
                    resp.text[:500] if resp.text else "",
                )
                resp.raise_for_status()
                data = resp.json()

                # DingTalk API may return HTTP 200 but body contains error code
                if "errcode" in data and data["errcode"] != 0:
                    errcode = data.get("errcode")
                    errmsg = data.get("errmsg", "Unknown error")
                    logger.error(
                        "[DingTalk SSO] Userinfo API error: errcode=%s errmsg=%s",
                        errcode,
                        errmsg,
                    )
                    raise AuthenticationError(
                        f"DingTalk userinfo API error: [{errcode}] {errmsg}"
                    )

                return data

        except httpx.HTTPStatusError as exc:
            # HTTP error status code, log response body for debugging
            logger.error(
                "[DingTalk SSO] Userinfo HTTP error: status=%s body=%s",
                exc.response.status_code,
                exc.response.text[:500] if exc.response.text else "",
            )
            raise AuthenticationError(
                f"DingTalk userinfo request failed: HTTP {exc.response.status_code}"
            ) from exc

        except httpx.RequestError as exc:
            # Network/connection error
            logger.error("[DingTalk SSO] Userinfo request error: %s", exc)
            raise AuthenticationError(
                f"DingTalk userinfo request failed: {exc}"
            ) from exc

    async def complete_login(self, code: str, code_verifier: str = "") -> AuthResult:
        """
        Complete DingTalk SSO login flow.

        与标准 OIDC 的区别:
        - Token 响应使用 camelCase: accessToken（不是 access_token）
        - 没有 id_token，跳过 JWT 验证
        - 用户标识从 userinfo 获取（unionId/openId）

        Args:
            code: 授权码。
            code_verifier: PKCE code_verifier（如果启用）。

        Returns:
            AuthResult 包含用户身份信息。

        Raises:
            AuthenticationError: 登录流程失败时。
        """
        # 1. Exchange authorization code for token
        tokens = await self.exchange_code(code, code_verifier)

        # DingTalk uses camelCase: accessToken (not access_token)
        access_token = tokens.get("accessToken", "")
        if not access_token:
            logger.error(
                "[DingTalk SSO] No accessToken in response: %s",
                list(tokens.keys()),
            )
            raise AuthenticationError("No accessToken in DingTalk token response")

        # 2. Fetch user info (fetch_userinfo raises AuthenticationError on failure)
        userinfo = await self.fetch_userinfo(access_token)

        # 3. Extract user identifier from userinfo
        # Prefer unionId (cross-app unique), fallback to openId (app-local unique)
        subject = userinfo.get("unionId") or userinfo.get("openId", "")
        if not subject:
            logger.error(
                "[DingTalk SSO] No unionId/openId in userinfo: %s",
                list(userinfo.keys()),
            )
            raise AuthenticationError(
                "Missing unionId/openId in DingTalk userinfo response"
            )

        # 4. Build AuthResult
        result = AuthResult(
            subject=subject,
            display_name=userinfo.get("nick", ""),
            email=userinfo.get("email", ""),
            roles=[],
            tenant_id=self._corp_id if self._corp_id else "default",
            raw_token=access_token,
            id_token="",  # DingTalk does not return id_token
            extra={
                "unionId": userinfo.get("unionId", ""),
                "openId": userinfo.get("openId", ""),
                "mobile": userinfo.get("mobile", ""),
                "avatarUrl": userinfo.get("avatarUrl", ""),
                "stateCode": userinfo.get("stateCode", ""),
            },
        )

        logger.info(
            "[DingTalk SSO] Login completed: subject=%s display_name=%s tenant_id=%s",
            result.subject,
            result.display_name,
            result.tenant_id,
        )

        return result
