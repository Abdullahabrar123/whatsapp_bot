"""Unit tests for phone number normalization, encryption, masking and hashing."""

import pytest

from app.core.exceptions import ValidationError
from app.core.security import FieldCipher
from app.domain.phone import normalise_phone

SECRET = "super_secret_test_key_minimum_32_characters_long_12345"


def test_phone_normalisation_valid():
    norm = normalise_phone("+44 7400 123456", key=SECRET)
    assert norm.e164 == "+447400123456"
    assert norm.masked.endswith("56")
    assert "*" in norm.masked
    assert len(norm.hash) == 64  # sha256 hex


def test_phone_normalisation_invalid():
    with pytest.raises(ValidationError):
        normalise_phone("not_a_phone_number", key=SECRET)


def test_field_cipher_encryption_decryption():
    cipher = FieldCipher(SECRET)
    plaintext = "Hello, secret message with medical data."
    encrypted = cipher.encrypt(plaintext)
    assert encrypted is not None
    assert encrypted != plaintext
    assert "secret" not in encrypted

    decrypted = cipher.decrypt(encrypted)
    assert decrypted == plaintext
