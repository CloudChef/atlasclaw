# -*- coding: utf-8 -*-
"""Service operations for audit logging."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.db.models import AuditLogModel

logger = logging.getLogger(__name__)


class AuditService:
    """Service operations for audit logging."""

    @staticmethod
    async def log_audit(
        session: AsyncSession,
        entity_type: str,
        entity_id: str,
        action: str,
        user_id: Optional[str] = None,
        old_value: Optional[Dict[str, Any]] = None,
        new_value: Optional[Dict[str, Any]] = None,
    ) -> AuditLogModel:
        """Create an audit log entry.

        Args:
            session: Database session
            entity_type: Type of entity being modified (e.g., "user", "agent")
            entity_id: ID of the entity being modified
            action: Action performed ("CREATE", "UPDATE", "DELETE")
            user_id: ID of the user performing the action
            old_value: Previous state of the entity (for UPDATE/DELETE)
            new_value: New state of the entity (for CREATE/UPDATE)

        Returns:
            Created AuditLogModel instance
        """
        audit = AuditLogModel(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            user_id=user_id,
            old_value=old_value,
            new_value=new_value,
        )
        session.add(audit)
        await session.flush()

        logger.debug(
            f"Audit log created: {action} {entity_type}:{entity_id} by user {user_id}"
        )
        return audit

    @staticmethod
    def sanitize_user_data(user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive fields from user data for audit logging.

        Args:
            user_data: User data dictionary

        Returns:
            Sanitized user data without sensitive fields
        """
        # Create a copy to avoid modifying the original
        sanitized = dict(user_data)
        # Remove password and other sensitive fields
        sensitive_fields = ["password", "api_key", "api_key_encrypted"]
        for field in sensitive_fields:
            sanitized.pop(field, None)
        return sanitized
