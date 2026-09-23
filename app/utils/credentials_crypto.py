"""Fernet encryption for the AI provider API keys stored in MongoDB.

The master key is AI_CREDENTIALS_ENCRYPTION_KEY (app config). It is read at
call time, never cached, and never logged or included in an exception
message (MULTI_AI_PROVIDER_DESIGN.md Section 4, Appendix A).
"""

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class EncryptionKeyUnavailable(Exception):
    """AI_CREDENTIALS_ENCRYPTION_KEY is missing or not a valid Fernet key."""


class EncryptionKeyMissing(EncryptionKeyUnavailable):
    pass


class EncryptionKeyInvalid(EncryptionKeyUnavailable):
    pass


class StoredKeyUnreadable(Exception):
    """A stored API key can't be decrypted -- the encryption key changed or was lost."""


def encryption_key_status() -> str:
    """Return "ok", "missing", or "invalid" -- for the admin page's banner."""
    try:
        _fernet()
    except EncryptionKeyMissing:
        return "missing"
    except EncryptionKeyInvalid:
        return "invalid"
    return "ok"


def _fernet() -> Fernet:
    key = current_app.config.get("AI_CREDENTIALS_ENCRYPTION_KEY")
    if not key:
        raise EncryptionKeyMissing("AI_CREDENTIALS_ENCRYPTION_KEY is not set")
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        # `from None` -- keep the malformed key's value out of any traceback.
        raise EncryptionKeyInvalid("AI_CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key") from None


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    fernet = _fernet()
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        raise StoredKeyUnreadable("stored API key can't be decrypted with the current encryption key") from None
