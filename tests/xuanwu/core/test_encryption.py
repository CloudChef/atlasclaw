# -*- coding: utf-8 -*-
"""
Tests for the AES-256-GCM encryption service.

This module contains comprehensive tests for the EncryptionService class,
following TDD principles with full coverage of encryption/decryption operations.
"""

import base64
import os
from unittest.mock import patch

import pytest

from app.xuanwu.core.encryption import EncryptionService, EncryptionError


class TestEncryptionService:
    """Test suite for EncryptionService class."""

    @pytest.fixture
    def valid_key(self) -> str:
        """Provide a valid 32-byte base64-encoded encryption key."""
        return base64.b64encode(b"x" * 32).decode("utf-8")

    @pytest.fixture
    def encryption_service(self, valid_key: str) -> EncryptionService:
        """Create an EncryptionService instance with a valid key."""
        with patch.dict(os.environ, {"XUANWU_ENCRYPTION_KEY": valid_key}):
            return EncryptionService()

    def test_encrypt_decrypt_roundtrip(self, encryption_service: EncryptionService) -> None:
        """Test that encryption and decryption produce the original plaintext."""
        plaintext = "Hello, World! 你好世界 🌍"

        ciphertext = encryption_service.encrypt(plaintext)
        decrypted = encryption_service.decrypt(ciphertext)

        assert decrypted == plaintext
        assert ciphertext.startswith("v1:")

    def test_encrypt_json_roundtrip(self, encryption_service: EncryptionService) -> None:
        """Test encryption and decryption of JSON-serializable data."""
        data = {
            "user_id": 12345,
            "name": "John Doe",
            "active": True,
            "metadata": {"tags": ["test", "encryption"]},
        }

        ciphertext = encryption_service.encrypt_json(data)
        decrypted = encryption_service.decrypt_json(ciphertext)

        assert decrypted == data
        assert ciphertext.startswith("v1:")

    def test_uses_default_key_when_no_env_var(self) -> None:
        """Test that hardcoded default key is used when no env var is set."""
        # Ensure key is not in environment
        with patch.dict(os.environ, {}, clear=True):
            # Should succeed with hardcoded default key
            service = EncryptionService()
            assert service._current_key_id == "default"
            
            # Verify encryption/decryption works
            plaintext = "test data"
            ciphertext = service.encrypt(plaintext)
            decrypted = service.decrypt(ciphertext)
            assert decrypted == plaintext

    def test_tamper_detection(self, encryption_service: EncryptionService) -> None:
        """Test that tampering with ciphertext is detected by GCM authentication tag."""
        plaintext = "Sensitive data that must not be tampered with"
        ciphertext = encryption_service.encrypt(plaintext)

        # New format: v1:key_id:base64data
        parts = ciphertext.split(":", 2)
        assert len(parts) == 3
        version, key_id, b64_data = parts

        decoded = base64.b64decode(b64_data)
        tampered = decoded[:20] + bytes([decoded[20] ^ 0xFF]) + decoded[21:]
        tampered_b64 = base64.b64encode(tampered).decode("utf-8")
        tampered_ciphertext = f"{version}:{key_id}:{tampered_b64}"

        # Decryption should fail due to authentication tag mismatch
        with pytest.raises(EncryptionError):
            encryption_service.decrypt(tampered_ciphertext)

    def test_different_keys_produce_different_ciphertext(
        self, valid_key: str
    ) -> None:
        """Test that encryption with different keys produces different ciphertexts."""
        plaintext = "Same plaintext"

        # Create two services with different keys
        key1 = valid_key
        key2 = base64.b64encode(b"y" * 32).decode("utf-8")

        with patch.dict(os.environ, {"XUANWU_ENCRYPTION_KEY": key1}):
            service1 = EncryptionService()

        with patch.dict(os.environ, {"XUANWU_ENCRYPTION_KEY": key2}):
            service2 = EncryptionService()

        # Encrypt with both services
        ciphertext1 = service1.encrypt(plaintext)
        ciphertext2 = service2.encrypt(plaintext)

        # Ciphertexts should be different
        assert ciphertext1 != ciphertext2

        # Each service can only decrypt its own ciphertext
        assert service1.decrypt(ciphertext1) == plaintext
        assert service2.decrypt(ciphertext2) == plaintext

        with pytest.raises(EncryptionError):
            service1.decrypt(ciphertext2)

        with pytest.raises(EncryptionError):
            service2.decrypt(ciphertext1)

    def test_invalid_format_raises_error(self, encryption_service: EncryptionService) -> None:
        """Test that malformed ciphertext raises an error."""
        invalid_ciphertexts = [
            "",  # Empty string
            "not-a-version-prefix:data",  # Missing v1: prefix
            "v1:",  # Missing data
            "v1:not-valid-base64!!!",  # Invalid base64
            "v1:" + base64.b64encode(b"too-short").decode("utf-8"),  # Too short for GCM
        ]

        for invalid in invalid_ciphertexts:
            with pytest.raises(EncryptionError):
                encryption_service.decrypt(invalid)

    def test_invalid_json_raises_error(self, encryption_service: EncryptionService) -> None:
        """Test that decrypting non-JSON data with decrypt_json raises an error."""
        # Encrypt a plain string, then try to decrypt as JSON
        plaintext = "This is not valid JSON"
        ciphertext = encryption_service.encrypt(plaintext)

        with pytest.raises(EncryptionError):
            encryption_service.decrypt_json(ciphertext)

    def test_encrypt_produces_different_nonces(
        self, encryption_service: EncryptionService
    ) -> None:
        """Test that each encryption uses a unique nonce."""
        plaintext = "Same plaintext"

        ciphertext1 = encryption_service.encrypt(plaintext)
        ciphertext2 = encryption_service.encrypt(plaintext)

        # Same plaintext should produce different ciphertexts (due to random nonce)
        assert ciphertext1 != ciphertext2

        # But both should decrypt to the same plaintext
        assert encryption_service.decrypt(ciphertext1) == plaintext
        assert encryption_service.decrypt(ciphertext2) == plaintext

    def test_decrypt_with_wrong_version(self, encryption_service: EncryptionService) -> None:
        """Test that decrypting with unsupported version prefix raises error."""
        plaintext = "Test data"
        ciphertext = encryption_service.encrypt(plaintext)

        # Replace v1: with v2:
        wrong_version = "v2:" + ciphertext.split(":", 1)[1]

        with pytest.raises(EncryptionError):
            encryption_service.decrypt(wrong_version)

    def test_key_not_32_bytes_raises_error(self) -> None:
        """Test that keys not exactly 32 bytes raise an error."""
        invalid_keys = [
            base64.b64encode(b"short").decode("utf-8"),  # Too short
            base64.b64encode(b"x" * 31).decode("utf-8"),  # 31 bytes
            base64.b64encode(b"x" * 33).decode("utf-8"),  # 33 bytes
        ]

        for invalid_key in invalid_keys:
            with patch.dict(os.environ, {"XUANWU_ENCRYPTION_KEY": invalid_key}):
                with pytest.raises(EncryptionError):
                    EncryptionService()

    def test_empty_plaintext(self, encryption_service: EncryptionService) -> None:
        """Test encryption and decryption of empty string."""
        plaintext = ""

        ciphertext = encryption_service.encrypt(plaintext)
        decrypted = encryption_service.decrypt(ciphertext)

        assert decrypted == plaintext

    def test_unicode_handling(self, encryption_service: EncryptionService) -> None:
        """Test proper handling of various Unicode characters."""
        test_cases = [
            "Hello, 世界! 🌍",
            "Привет мир",
            "مرحبا بالعالم",
            "👨‍👩‍👧‍👦 Family emoji",
            "\x00\x01\x02\x03",  # Control characters
        ]

        for plaintext in test_cases:
            ciphertext = encryption_service.encrypt(plaintext)
            decrypted = encryption_service.decrypt(ciphertext)
            assert decrypted == plaintext
