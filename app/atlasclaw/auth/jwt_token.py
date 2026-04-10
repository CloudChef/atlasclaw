from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.atlasclaw.auth.models import AuthenticationError


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * ((4 - (len(data) % 4)) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("utf-8"))


def _json_dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def issue_atlas_token(
    *,
    subject: str,
    is_admin: bool,
    roles: list[str],
    auth_type: str,
    secret_key: str,
    expires_minutes: int,
    issuer: str,
    login_time: Optional[str] = None,
    additional_claims: Optional[dict[str, Any]] = None,
) -> str:
    if not secret_key:
        raise AuthenticationError("JWT secret key is empty")
    if not subject:
        raise AuthenticationError("JWT subject is empty")

    now = int(time.time())
    exp = now + max(60, int(expires_minutes) * 60)
    login_time_str = login_time or datetime.now(timezone.utc).isoformat()

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "iss": issuer,
        "iat": now,
        "exp": exp,
        "login_time": login_time_str,
        "is_admin": bool(is_admin),
        "admin": bool(is_admin),
        "roles": roles,
        "auth_type": auth_type,
    }
    if isinstance(additional_claims, dict):
        for key, value in additional_claims.items():
            if key in payload or value is None:
                continue
            payload[key] = value

    header_b64 = _b64url_encode(_json_dumps(header).encode("utf-8"))
    payload_b64 = _b64url_encode(_json_dumps(payload).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def verify_atlas_token(*, token: str, secret_key: str, issuer: str) -> dict[str, Any]:
    if not token:
        raise AuthenticationError("JWT token is empty")
    if not secret_key:
        raise AuthenticationError("JWT secret key is empty")

    parts = token.split(".")
    if len(parts) != 3:
        raise AuthenticationError("Invalid JWT format")

    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise AuthenticationError(f"Invalid JWT payload: {exc}") from exc

    if header.get("alg") != "HS256":
        raise AuthenticationError("Unsupported JWT algorithm")

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise AuthenticationError("JWT signature verification failed")

    now = int(time.time())
    exp = int(payload.get("exp", 0))
    iat = int(payload.get("iat", 0))

    if exp <= now:
        raise AuthenticationError("JWT token has expired")
    if iat > now + 60:
        raise AuthenticationError("JWT iat is invalid")
    if issuer and payload.get("iss") != issuer:
        raise AuthenticationError("JWT issuer mismatch")
    if not payload.get("sub"):
        raise AuthenticationError("JWT subject is missing")
    if "login_time" not in payload:
        raise AuthenticationError("JWT login_time is missing")

    return payload
