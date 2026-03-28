# -*- coding: utf-8 -*-
"""AES-256-GCM encryption service for sensitive data.

This module provides encryption/decryption services using AES-256-GCM algorithm.
All sensitive data (API keys, passwords, credentials) should be encrypted at rest.

Format: v1:base64(nonce(12) + ciphertext + tag(16))
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# Version prefix for ciphertext format
FORMAT_VERSION = "v1"
FORMAT_PREFIX = f"{FORMAT_VERSION}:"

# Envelope encryption version
ENVELOPE_VERSION = "v2"
ENVELOPE_PREFIX = f"{ENVELOPE_VERSION}:"

# Hardcoded default encryption key (32 bytes, base64 encoded)
# This is the default key used for encryption/decryption.
# Override via XUANWU_ENCRYPTION_KEY environment variable if needed.
# Generated: base64.b64encode(b"xuanwu-default-32byte-key!!!!")
DEFAULT_ENCRYPTION_KEY = "YXRsYXNjbGF3LWRlZmF1bHQtMzJieXRlLWtleSEhISE="

# Key rotation support - environment variable pattern for multiple keys
# XUANWU_ENCRYPTION_KEY - overrides default key
# XUANWU_ENCRYPTION_KEY_20240325 - additional key with ID
# Format: XUANWU_ENCRYPTION_KEY_<KEY_ID>


class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""
    pass


class MissingKeyError(EncryptionError):
    """Raised when encryption key is not configured."""
    pass


class InvalidCiphertextError(EncryptionError):
    """Raised when ciphertext format is invalid or tampered."""
    pass


class EncryptionService:
    """AES-256-GCM encryption service with key rotation support.
    
    Usage:
        service = EncryptionService()  # Uses hardcoded default key
        encrypted = service.encrypt("sensitive data")
        decrypted = service.decrypt(encrypted)
    
    The service uses a hardcoded default encryption key (DEFAULT_ENCRYPTION_KEY).
    To use a custom key, set the XUANWU_ENCRYPTION_KEY environment variable.
    
    Key Rotation:
        - Set XUANWU_ENCRYPTION_KEY to the new key
        - Old data can still be decrypted if old key is available as
          XUANWU_ENCRYPTION_KEY_<key_id>
    """
    
    def __init__(self, key: bytes | None = None, key_id: str = "default") -> None:
        """Initialize encryption service.
        
        Args:
            key: Optional 32-byte encryption key. If not provided, reads from
                 XUANWU_ENCRYPTION_KEY environment variable.
            key_id: Identifier for this key (used in key rotation).
        
        Raises:
            MissingKeyError: If no key is provided and env var is not set.
            ValueError: If key is not 32 bytes.
        """
        self._key_id = key_id
        self._keys: dict[str, bytes] = {}
        
        if key is not None:
            self._validate_key(key)
            self._keys[key_id] = key
            self._current_key_id = key_id
        else:
            self._load_keys_from_env()
        
        logger.debug(f"EncryptionService initialized with {len(self._keys)} key(s)")
    
    def _validate_key(self, key: bytes) -> None:
        """Validate key length."""
        if len(key) != 32:
            raise EncryptionError(f"Encryption key must be 32 bytes (256-bit), got {len(key)} bytes")
    
    def _load_keys_from_env(self) -> None:
        """Load encryption keys.
        
        Uses hardcoded DEFAULT_ENCRYPTION_KEY as the default.
        Can be overridden via XUANWU_ENCRYPTION_KEY environment variable.
        Additional keys can be loaded via XUANWU_ENCRYPTION_KEY_<key_id>.
        """
        # Load default key - use hardcoded key, allow env override
        default_key_b64 = os.environ.get("XUANWU_ENCRYPTION_KEY", DEFAULT_ENCRYPTION_KEY)
        
        try:
            default_key = base64.b64decode(default_key_b64)
            self._validate_key(default_key)
            self._keys["default"] = default_key
            self._current_key_id = "default"
            
            # Log if using custom key from environment
            if default_key_b64 != DEFAULT_ENCRYPTION_KEY:
                logger.info("Using custom encryption key from XUANWU_ENCRYPTION_KEY")
            else:
                logger.debug("Using default hardcoded encryption key")
        except Exception as e:
            raise EncryptionError(f"Failed to load default encryption key: {e}") from e
        
        # Load additional keys for rotation (XUANWU_ENCRYPTION_KEY_<key_id>)
        for env_name, env_value in os.environ.items():
            if env_name.startswith("XUANWU_ENCRYPTION_KEY_") and env_name != "XUANWU_ENCRYPTION_KEY":
                key_id = env_name[len("XUANWU_ENCRYPTION_KEY_"):]
                try:
                    key = base64.b64decode(env_value)
                    self._validate_key(key)
                    self._keys[key_id] = key
                    logger.debug(f"Loaded encryption key: {key_id}")
                except Exception as e:
                    logger.warning(f"Failed to load key {key_id}: {e}")
    
    def encrypt(self, plaintext: str, key_id: str | None = None) -> str:
        """Encrypt plaintext string.
        
        Args:
            plaintext: String to encrypt.
            key_id: Optional key ID to use. If not provided, uses current default key.
        
        Returns:
            Encrypted ciphertext in format: v1:key_id:base64(nonce+ciphertext+tag)
        
        Raises:
            EncryptionError: If encryption fails.
            MissingKeyError: If specified key_id is not available.
        """
        try:
            use_key_id = key_id or self._current_key_id
            if use_key_id not in self._keys:
                raise MissingKeyError(f"Encryption key '{use_key_id}' not available")
            
            key = self._keys[use_key_id]
            aesgcm = AESGCM(key)
            
            # Generate random 12-byte nonce (96-bit for GCM)
            nonce = os.urandom(12)
            
            # Encrypt plaintext
            ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
            
            # Format: nonce(12) + ciphertext + tag(16)
            combined = nonce + ciphertext
            
            # Encode as base64 with version and key_id prefix
            return f"{FORMAT_PREFIX}{use_key_id}:{base64.b64encode(combined).decode('ascii')}"
        except EncryptionError:
            raise
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt data: {e}") from e
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext string.
        
        Args:
            ciphertext: Encrypted string in format: v1:[key_id:]base64(nonce+ciphertext+tag)
                       (key_id is optional for backward compatibility)
        
        Returns:
            Decrypted plaintext string.
        
        Raises:
            InvalidCiphertextError: If format is invalid or data is tampered.
            EncryptionError: If decryption fails.
        """
        try:
            # Check version prefix
            if not ciphertext.startswith(FORMAT_PREFIX):
                raise InvalidCiphertextError(
                    f"Invalid ciphertext format. Expected prefix '{FORMAT_PREFIX}'"
                )
            
            # Extract payload after version prefix
            payload = ciphertext[len(FORMAT_PREFIX):]
            
            # Check if key_id is present (new format) or not (old format)
            if ":" in payload:
                key_id, payload_b64 = payload.split(":", 1)
            else:
                # Backward compatibility: no key_id, use default
                key_id = "default"
                payload_b64 = payload
            
            if key_id not in self._keys:
                raise MissingKeyError(f"Cannot decrypt: key '{key_id}' not available")
            
            key = self._keys[key_id]
            aesgcm = AESGCM(key)
            
            # Decode base64
            combined = base64.b64decode(payload_b64)
            
            # Extract nonce (first 12 bytes)
            if len(combined) < 28:  # 12 (nonce) + 16 (tag) minimum
                raise InvalidCiphertextError("Ciphertext too short")
            
            nonce = combined[:12]
            ciphertext_with_tag = combined[12:]
            
            # Decrypt (AESGCM.decrypt expects ciphertext with tag appended)
            plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
            
            return plaintext.decode("utf-8")
        except (InvalidCiphertextError, MissingKeyError):
            raise
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise InvalidCiphertextError(f"Failed to decrypt data: {e}") from e
    
    def encrypt_json(self, data: dict[str, Any], key_id: str | None = None) -> str:
        """Encrypt a JSON-serializable dictionary.
        
        Args:
            data: Dictionary to encrypt.
            key_id: Optional key ID to use.
        
        Returns:
            Encrypted ciphertext.
        """
        plaintext = json.dumps(data, ensure_ascii=False)
        return self.encrypt(plaintext, key_id)
    
    def decrypt_json(self, ciphertext: str) -> dict[str, Any]:
        """Decrypt ciphertext to a dictionary.
        
        Args:
            ciphertext: Encrypted JSON data.
        
        Returns:
            Decrypted dictionary.
        
        Raises:
            EncryptionError: If JSON parsing fails.
        """
        plaintext = self.decrypt(ciphertext)
        try:
            return json.loads(plaintext)
        except json.JSONDecodeError as e:
            raise EncryptionError(f"Failed to parse decrypted data as JSON: {e}") from e
    
    def rotate_key(self, new_key: bytes | None = None) -> str:
        """Rotate to a new encryption key.
        
        Args:
            new_key: Optional new 32-byte key. If not provided, generates a random key.
        
        Returns:
            Key ID for the new key.
        """
        import time
        
        if new_key is None:
            new_key = os.urandom(32)
        
        self._validate_key(new_key)
        
        # Generate key_id based on timestamp
        key_id = time.strftime("%Y%m%d%H%M%S")
        
        self._keys[key_id] = new_key
        self._current_key_id = key_id
        
        logger.info(f"Encryption key rotated to: {key_id}")
        return key_id
    
    def get_available_key_ids(self) -> list[str]:
        """Get list of available key IDs."""
        return list(self._keys.keys())


class EnvelopeEncryptionService:
    """Envelope encryption service using data keys encrypted by master key.

    Each encryption operation generates a unique data key.
    The data key is encrypted with the master key and stored alongside ciphertext.
    This allows for key rotation without re-encrypting all data.

    Uses hardcoded DEFAULT_ENCRYPTION_KEY as the default master key.
    Can be overridden via XUANWU_MASTER_KEY environment variable.

    Format: v2:base64(encrypted_data_key + nonce + ciphertext + tag)
    """
    
    def __init__(self, master_key: bytes | None = None) -> None:
        """Initialize envelope encryption service.
        
        Args:
            master_key: Optional 32-byte master key. If not provided, uses the
                       hardcoded DEFAULT_ENCRYPTION_KEY or XUANWU_MASTER_KEY env var.
        """
        if master_key is None:
            # Try env vars first, then fall back to hardcoded default
            master_key_b64 = os.environ.get("XUANWU_MASTER_KEY") or \
                           os.environ.get("XUANWU_ENCRYPTION_KEY") or \
                           DEFAULT_ENCRYPTION_KEY
            
            try:
                master_key = base64.b64decode(master_key_b64)
            except Exception as e:
                raise EncryptionError(f"Failed to decode master key: {e}") from e
        
        if len(master_key) != 32:
            raise EncryptionError(f"Master key must be 32 bytes (256-bit), got {len(master_key)} bytes")
        
        self._master_key = master_key
        self._master_aesgcm = AESGCM(master_key)
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt using envelope encryption.
        
        1. Generate random data key
        2. Encrypt plaintext with data key
        3. Encrypt data key with master key
        4. Combine: encrypted_data_key + nonce + ciphertext + tag
        
        Args:
            plaintext: String to encrypt.
        
        Returns:
            Encrypted ciphertext in envelope format.
        """
        try:
            # Generate random data key (32 bytes)
            data_key = os.urandom(32)
            
            # Encrypt data key with master key
            data_key_nonce = os.urandom(12)
            encrypted_data_key = self._master_aesgcm.encrypt(data_key_nonce, data_key, None)
            
            # Encrypt plaintext with data key
            data_aesgcm = AESGCM(data_key)
            plaintext_nonce = os.urandom(12)
            ciphertext = data_aesgcm.encrypt(plaintext_nonce, plaintext.encode("utf-8"), None)
            
            # Combine all parts
            # Format: data_key_nonce(12) + encrypted_data_key(48) + plaintext_nonce(12) + ciphertext
            combined = data_key_nonce + encrypted_data_key + plaintext_nonce + ciphertext
            
            return f"{ENVELOPE_PREFIX}{base64.b64encode(combined).decode('ascii')}"
        except Exception as e:
            logger.error(f"Envelope encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt data: {e}") from e
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt envelope-encrypted ciphertext.
        
        1. Extract encrypted data key
        2. Decrypt data key with master key
        3. Decrypt ciphertext with data key
        
        Args:
            ciphertext: Envelope-encrypted string.
        
        Returns:
            Decrypted plaintext.
        """
        try:
            if not ciphertext.startswith(ENVELOPE_PREFIX):
                raise InvalidCiphertextError(
                    f"Invalid envelope format. Expected prefix '{ENVELOPE_PREFIX}'"
                )
            
            # Decode base64
            combined = base64.b64decode(ciphertext[len(ENVELOPE_PREFIX):])
            
            # Minimum size: 12 (dk_nonce) + 48 (encrypted_dk) + 12 (pt_nonce) + 16 (tag)
            if len(combined) < 88:
                raise InvalidCiphertextError("Envelope ciphertext too short")
            
            # Extract parts
            data_key_nonce = combined[:12]
            encrypted_data_key = combined[12:60]  # 48 bytes
            plaintext_nonce = combined[60:72]
            ciphertext_with_tag = combined[72:]
            
            # Decrypt data key
            data_key = self._master_aesgcm.decrypt(data_key_nonce, encrypted_data_key, None)
            
            # Decrypt plaintext with data key
            data_aesgcm = AESGCM(data_key)
            plaintext = data_aesgcm.decrypt(plaintext_nonce, ciphertext_with_tag, None)
            
            return plaintext.decode("utf-8")
        except (InvalidCiphertextError, EncryptionError):
            raise
        except Exception as e:
            logger.error(f"Envelope decryption failed: {e}")
            raise InvalidCiphertextError(f"Failed to decrypt data: {e}") from e


# Global singleton instances
_encryption_service: EncryptionService | None = None
_envelope_service: EnvelopeEncryptionService | None = None


def get_encryption_service() -> EncryptionService:
    """Get or create the global encryption service instance.
    
    Returns:
        EncryptionService singleton instance.
    
    Raises:
        MissingKeyError: If encryption key is not configured.
    """
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service


def get_envelope_service() -> EnvelopeEncryptionService:
    """Get or create the global envelope encryption service instance.
    
    Returns:
        EnvelopeEncryptionService singleton instance.
    
    Raises:
        MissingKeyError: If master key is not configured.
    """
    global _envelope_service
    if _envelope_service is None:
        _envelope_service = EnvelopeEncryptionService()
    return _envelope_service


def encrypt(plaintext: str, key_id: str | None = None) -> str:
    """Convenience function: Encrypt plaintext using global service.
    
    Args:
        plaintext: String to encrypt.
        key_id: Optional key ID to use.
    
    Returns:
        Encrypted ciphertext.
    """
    return get_encryption_service().encrypt(plaintext, key_id)


def decrypt(ciphertext: str) -> str:
    """Convenience function: Decrypt ciphertext using global service.
    
    Args:
        ciphertext: Encrypted string.
    
    Returns:
        Decrypted plaintext.
    """
    return get_encryption_service().decrypt(ciphertext)


def encrypt_json(data: dict[str, Any], key_id: str | None = None) -> str:
    """Convenience function: Encrypt JSON data using global service.
    
    Args:
        data: Dictionary to encrypt.
        key_id: Optional key ID to use.
    
    Returns:
        Encrypted ciphertext.
    """
    return get_encryption_service().encrypt_json(data, key_id)


def decrypt_json(ciphertext: str) -> dict[str, Any]:
    """Convenience function: Decrypt JSON data using global service.
    
    Args:
        ciphertext: Encrypted JSON data.
    
    Returns:
        Decrypted dictionary.
    """
    return get_encryption_service().decrypt_json(ciphertext)


def envelope_encrypt(plaintext: str) -> str:
    """Convenience function: Envelope encrypt plaintext.
    
    Args:
        plaintext: String to encrypt.
    
    Returns:
        Envelope-encrypted ciphertext.
    """
    return get_envelope_service().encrypt(plaintext)


def envelope_decrypt(ciphertext: str) -> str:
    """Convenience function: Envelope decrypt ciphertext.
    
    Args:
        ciphertext: Envelope-encrypted string.
    
    Returns:
        Decrypted plaintext.
    """
    return get_envelope_service().decrypt(ciphertext)
