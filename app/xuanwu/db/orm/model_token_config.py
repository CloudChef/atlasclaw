# -*- coding: utf-8 -*-
"""Service operations for Token configuration."""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime
from typing import List, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.xuanwu.core.encryption import encrypt, decrypt, FORMAT_PREFIX
from app.xuanwu.db.models import TokenModel
from app.xuanwu.db.schemas import TokenCreate, TokenUpdate

logger = logging.getLogger(__name__)

# LEGACY: Old Fernet-based encryption for backward compatibility
# Will be removed in future version after all data is migrated
_LEGACY_ENCRYPTION_KEY = os.environ.get("XUANWU_ENCRYPTION_KEY")


def _get_fernet() -> Fernet | None:
    """Get Fernet instance for legacy decryption (backward compatibility).
    
    Returns None if legacy key is not available.
    """
    if not _LEGACY_ENCRYPTION_KEY:
        return None
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"xuanwu-salt",
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(_LEGACY_ENCRYPTION_KEY.encode()))
    return Fernet(key)


def _is_legacy_format(encrypted_key: str) -> bool:
    """Check if encrypted key uses legacy Fernet format.
    
    Legacy format: base64(Fernet ciphertext)
    New format: v1:base64(nonce+ciphertext+tag)
    """
    return not encrypted_key.startswith(FORMAT_PREFIX)


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key using AES-256-GCM.

    Args:
        api_key: Plain text API key

    Returns:
        Encrypted API key string in format: v1:base64(nonce+ciphertext+tag)
    """
    return encrypt(api_key)


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt an API key (supports both legacy and new format).

    Args:
        encrypted_key: Encrypted API key string

    Returns:
        Plain text API key

    Raises:
        EncryptionError: If decryption fails
    """
    # Check if it's legacy Fernet format
    if _is_legacy_format(encrypted_key):
        logger.debug("Detected legacy Fernet format, decrypting with backward compatibility")
        fernet = _get_fernet()
        if fernet is None:
            raise ValueError(
                "Cannot decrypt legacy Fernet format: XUANWU_ENCRYPTION_KEY not set"
            )
        return fernet.decrypt(encrypted_key.encode()).decode()
    
    # New AES-256-GCM format
    return decrypt(encrypted_key)


def mask_api_key(api_key: str) -> str:
    """Mask an API key for display.

    Args:
        api_key: Plain text API key

    Returns:
        Masked API key (e.g., "sk-xxx...xxx")
    """
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


class ModelTokenConfigService:
    """Service operations for Token configuration."""

    @staticmethod
    async def create(session: AsyncSession, token_data: TokenCreate) -> TokenModel:
        """Create a new Token.

        Args:
            session: Database session
            token_data: Token creation data

        Returns:
            Created Token model
        """
        # Encrypt API key if provided
        api_key_encrypted = None
        if token_data.api_key:
            api_key_encrypted = encrypt_api_key(token_data.api_key)

        token = TokenModel(
            name=token_data.name,
            provider=token_data.provider,
            model=token_data.model,
            base_url=token_data.base_url,
            api_key_encrypted=api_key_encrypted,
            priority=token_data.priority,
            weight=token_data.weight,
            is_active=token_data.is_active,
        )
        session.add(token)
        await session.flush()
        await session.refresh(token)
        logger.info(f"Created token: {token.name} (id={token.id})")
        return token

    @staticmethod
    async def get_by_id(session: AsyncSession, token_id: str) -> Optional[TokenModel]:
        """Get Token by ID.

        Args:
            session: Database session
            token_id: Token ID

        Returns:
            Token model or None
        """
        result = await session.execute(
            select(TokenModel).where(TokenModel.id == token_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(session: AsyncSession, name: str) -> Optional[TokenModel]:
        """Get Token by name.

        Args:
            session: Database session
            name: Token name

        Returns:
            Token model or None
        """
        result = await session.execute(
            select(TokenModel).where(TokenModel.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        provider: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[TokenModel], int]:
        """List all Tokens with optional filtering.

        Args:
            session: Database session
            provider: Filter by provider
            is_active: Filter by active status
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (list of tokens, total count)
        """
        query = select(TokenModel)

        if provider:
            query = query.where(TokenModel.provider == provider)
        if is_active is not None:
            query = query.where(TokenModel.is_active == is_active)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_query)).scalar()

        # Get paginated results
        query = query.offset((page - 1) * page_size).limit(page_size)
        query = query.order_by(TokenModel.priority.desc(), TokenModel.created_at.desc())

        result = await session.execute(query)
        tokens = list(result.scalars().all())

        return tokens, total

    @staticmethod
    async def list_active(session: AsyncSession) -> List[TokenModel]:
        """List all active Tokens for runtime use.

        Args:
            session: Database session

        Returns:
            List of active Token models sorted by priority
        """
        result = await session.execute(
            select(TokenModel)
            .where(TokenModel.is_active == True)
            .order_by(TokenModel.priority.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        session: AsyncSession, token_id: str, token_data: TokenUpdate
    ) -> Optional[TokenModel]:
        """Update a Token.

        Args:
            session: Database session
            token_id: Token ID
            token_data: Update data

        Returns:
            Updated Token model or None
        """
        token = await ModelTokenConfigService.get_by_id(session, token_id)
        if token is None:
            return None

        update_data = token_data.model_dump(exclude_unset=True)

        # Encrypt new API key if provided
        if "api_key" in update_data and update_data["api_key"]:
            update_data["api_key_encrypted"] = encrypt_api_key(update_data.pop("api_key"))
        elif "api_key" in update_data:
            del update_data["api_key"]

        for key, value in update_data.items():
            setattr(token, key, value)

        token.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(token)

        logger.info(f"Updated token: {token.name} (id={token.id})")
        return token

    @staticmethod
    async def delete(session: AsyncSession, token_id: str) -> bool:
        """Delete a Token.

        Args:
            session: Database session
            token_id: Token ID

        Returns:
            True if deleted, False if not found
        """
        token = await ModelTokenConfigService.get_by_id(session, token_id)
        if token is None:
            return False

        await session.delete(token)
        logger.info(f"Deleted token: {token.name} (id={token.id})")
        return True

    @staticmethod
    async def update_rate_limit(
        session: AsyncSession,
        token_id: str,
        remaining: Optional[int] = None,
        reset_at: Optional[datetime] = None,
    ) -> Optional[TokenModel]:
        """Update rate limit state for a Token.

        Args:
            session: Database session
            token_id: Token ID
            remaining: Remaining requests
            reset_at: Reset timestamp

        Returns:
            Updated Token model or None
        """
        token = await ModelTokenConfigService.get_by_id(session, token_id)
        if token is None:
            return None

        token.rate_limit_remaining = remaining
        token.rate_limit_reset = reset_at
        await session.flush()
        return token

    @staticmethod
    def get_decrypted_api_key(token: TokenModel) -> Optional[str]:
        """Get decrypted API key from a Token model.

        Args:
            token: Token model

        Returns:
            Decrypted API key or None
        """
        if token.api_key_encrypted is None:
            return None
        try:
            return decrypt_api_key(token.api_key_encrypted)
        except Exception as e:
            logger.error(f"Failed to decrypt API key for token {token.id}: {e}")
            return None

    @staticmethod
    def get_masked_api_key(token: TokenModel) -> Optional[str]:
        """Get masked API key for display.

        Args:
            token: Token model

        Returns:
            Masked API key or None
        """
        api_key = ModelTokenConfigService.get_decrypted_api_key(token)
        if api_key is None:
            return None
        return mask_api_key(api_key)
