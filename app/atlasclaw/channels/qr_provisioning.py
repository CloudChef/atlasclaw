# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""QR provisioning contracts for channel one-click setup.

This module intentionally contains only value objects and small helpers shared
by channel handlers, the API layer, and persistence services. Storage,
authorization, polling, and connection creation live outside this module.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
import secrets
from typing import Any, Optional

try:
    import qrcode
except ImportError:  # pragma: no cover - requirements include qrcode; keep startup resilient.
    qrcode = None


TERMINAL_PROVISIONING_STATUSES = frozenset({"completed", "failed", "cancelled"})
USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp for provisioning state comparisons."""
    return datetime.now(timezone.utc)


def build_qr_image_data_url(value: str) -> Optional[str]:
    """Render a QR payload as an inline PNG data URL for the frontend modal."""
    if not value or qrcode is None:
        return None

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def normalize_user_code(value: str) -> str:
    """Normalize pairing codes before comparing locally tracked session state."""
    return value.replace("-", "").strip().upper()


def generate_user_code(groups: int = 3) -> str:
    """Generate a human-readable OpenClaw pairing code in grouped form."""
    code_groups = [
        "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(4))
        for _ in range(groups)
    ]
    return "-".join(code_groups)


def parse_positive_int(value: Any, *, default: int) -> int:
    """Parse a positive integer value, using the caller's protocol default when absent."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass
class ChannelProvisioningRequest:
    """Input contract for handler-specific provisioning startup.

    The API creates a DB-backed session first, then passes this request to the
    handler. Handlers use the session identifiers and pairing code to begin a
    platform or broker-owned registration flow, then store any pollable handle
    in `ChannelProvisioningStart.platform_state`. AtlasClaw does not expose a
    public provisioning callback endpoint.
    """

    user_id: str
    channel_type: str
    session_id: str
    state_token: str
    user_code: str
    expires_at: datetime
    base_url: str = ""


@dataclass
class ChannelProvisioningStart:
    """Handler result for a newly-started platform registration.

    `qr_url` is the canonical value encoded into the QR image. `user_code` and
    `platform_state` are optional platform-issued values used when a broker,
    such as a Feishu/OpenClaw registration service, owns part of the pairing
    state and AtlasClaw must poll or match against that platform-issued state.
    """

    qr_url: str
    expires_at: datetime
    qr_image_url: Optional[str] = None
    user_code: Optional[str] = None
    platform_state: dict[str, Any] = field(default_factory=dict)
    refresh_after_seconds: int = 60
    instructions_i18n_key: str = ""


@dataclass
class ChannelProvisioningConnection:
    """Canonical channel connection produced after provisioning completes.

    The `config` shape must match the handler's manual setup schema. Handlers
    return this object from their poll method only after the platform or broker
    has issued usable credentials, so QR provisioning and hand-entered
    configuration produce compatible saved connections.
    """

    name: str
    config: dict[str, Any]
    is_default: bool = False


@dataclass
class ChannelProvisioningSession:
    """Public runtime view of a short-lived provisioning session.

    The DB service persists the same logical fields, while handlers may mutate
    status, expiry, `platform_state`, and polling interval during platform
    registration. API responses can expose this object because it carries QR
    state and connection summaries, but not final channel secrets.
    """

    session_id: str
    user_id: str
    channel_type: str
    state_token: str
    user_code: str
    status: str = "pending"
    qr_url: str = ""
    qr_image_url: Optional[str] = None
    instructions_i18n_key: str = ""
    error: Optional[str] = None
    connection_id: Optional[str] = None
    connection_name: Optional[str] = None
    platform_state: dict[str, Any] = field(default_factory=dict)
    refresh_after_seconds: int = 60
    expires_at: datetime = field(default_factory=lambda: utcnow() + timedelta(minutes=5))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Return whether the session expiry time has passed."""
        return (now or utcnow()) >= self.expires_at

    def public_status(self, now: Optional[datetime] = None) -> str:
        """Return API-facing status with computed expiry for active sessions."""
        if self.status not in TERMINAL_PROVISIONING_STATUSES and self.is_expired(now):
            return "expired"
        return self.status


def mark_provisioning_poll_attempt(
    session: ChannelProvisioningSession,
    *,
    default_interval_seconds: int,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Throttle platform polling and record the latest poll attempt time.

    Returns the active interval when the caller should poll now, or ``None`` when
    the previous poll is still inside the platform-advertised interval.
    """
    state = dict(session.platform_state or {})
    interval = parse_positive_int(
        state.get("interval"),
        default=default_interval_seconds,
    )
    current_time = now or utcnow()
    last_poll_at = state.get("last_poll_at")
    if last_poll_at:
        try:
            elapsed = (current_time - datetime.fromisoformat(str(last_poll_at))).total_seconds()
        except (TypeError, ValueError):
            elapsed = interval
        if elapsed < interval:
            return None

    state["last_poll_at"] = current_time.isoformat()
    session.platform_state = state
    session.updated_at = current_time
    return interval
