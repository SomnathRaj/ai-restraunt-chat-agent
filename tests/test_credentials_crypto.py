import pytest

from app.utils import credentials_crypto
from app.utils.credentials_crypto import EncryptionKeyInvalid, EncryptionKeyMissing, StoredKeyUnreadable

OTHER_KEY = "7Z9vTDPOyI0OVqXnq9yL8fqg8Hc8ZbL2mD1VYzY0G5Q="


def test_round_trip_and_ciphertext_hides_the_plaintext(app):
    token = credentials_crypto.encrypt("sk-live-secret")
    assert "sk-live-secret" not in token
    assert credentials_crypto.decrypt(token) == "sk-live-secret"


def test_same_plaintext_encrypts_differently_each_time(app):
    assert credentials_crypto.encrypt("same") != credentials_crypto.encrypt("same")


def test_missing_encryption_key_refuses_to_encrypt(app):
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = None
    with pytest.raises(EncryptionKeyMissing):
        credentials_crypto.encrypt("sk-live-secret")
    assert credentials_crypto.encryption_key_status() == "missing"


def test_malformed_encryption_key_refuses_to_encrypt_without_leaking_it(app):
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = "not-a-real-fernet-key"
    with pytest.raises(EncryptionKeyInvalid) as info:
        credentials_crypto.encrypt("sk-live-secret")
    assert "not-a-real-fernet-key" not in str(info.value)
    assert info.value.__cause__ is None
    assert credentials_crypto.encryption_key_status() == "invalid"


def test_ciphertext_from_a_different_encryption_key_is_unreadable(app):
    token = credentials_crypto.encrypt("sk-live-secret")
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = OTHER_KEY
    with pytest.raises(StoredKeyUnreadable):
        credentials_crypto.decrypt(token)


def test_status_is_ok_with_a_valid_key(app):
    assert credentials_crypto.encryption_key_status() == "ok"
