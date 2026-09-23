"""AI provider credentials, managed from Admin -> AI Settings (MULTI_AI_PROVIDER_DESIGN.md Section 4).

One `ai_providers` document per provider in the fixed PROVIDERS catalog
(D3) -- the four rows are created by ensure_provider_documents() and can
only be edited, never added or deleted. Exactly one document has
active = True at a time (D4), enforced here, not in the admin view.

API keys are stored only as Fernet ciphertext (app/utils/credentials_crypto.py).
Nothing returned by list_providers()/get_active_summary() ever contains a
key, encrypted or not -- only decrypt_api_key() yields plaintext, and only
for the caller that is about to hand it to a provider SDK.
"""

from datetime import datetime, timezone

from app.models.db import get_db
from app.utils import credentials_crypto
from app.utils.credentials_crypto import EncryptionKeyUnavailable, StoredKeyUnreadable
from app.utils.errors import AppError

# Order here is display order on the admin page. `implemented` is flipped to
# True in the same change that registers the provider's adapter in
# app/ai/providers/registry.py (a test keeps the two in sync).
PROVIDERS = {
    "gemini": {"display_name": "Google Gemini", "implemented": True, "model_example": "gemini-flash-latest"},
    "openai": {"display_name": "OpenAI", "implemented": True, "model_example": "gpt-4o-mini"},
    "anthropic": {"display_name": "Anthropic Claude", "implemented": True, "model_example": "claude-opus-5"},
    "openrouter": {"display_name": "OpenRouter", "implemented": True, "model_example": "openai/gpt-4o-mini"},
}

MODEL_MAX_LENGTH = 200
API_KEY_MAX_LENGTH = 500

TEST_OK = "ok"
TEST_FAILED = "failed"

_NO_ID = {"_id": 0}

_ENCRYPTION_KEY_MESSAGE = (
    "AI_CREDENTIALS_ENCRYPTION_KEY is missing or invalid on the server, so API keys can't be saved or used. "
    "Ask whoever runs the server to set it."
)


def ensure_provider_documents(db):
    """Create any missing provider document. Idempotent -- never overwrites existing values."""
    for provider in PROVIDERS:
        db.ai_providers.update_one(
            {"provider": provider},
            {
                "$setOnInsert": {
                    "provider": provider,
                    "api_key_encrypted": None,
                    "key_updated_at": None,
                    "model": None,
                    "active": False,
                    "updated_at": None,
                    "updated_by": None,
                    "last_tested_at": None,
                    "last_test_result": None,
                }
            },
            upsert=True,
        )


def _require_provider(provider: str, *, implemented: bool = True) -> dict:
    meta = PROVIDERS.get(provider)
    if meta is None:
        raise AppError("unknown_provider", f"Unknown AI provider '{provider}'.", 404)
    if implemented and not meta["implemented"]:
        raise AppError("provider_not_available", f"{meta['display_name']} isn't available yet.", 400)
    return meta


def _get_doc(provider: str) -> dict:
    return get_db().ai_providers.find_one({"provider": provider}, _NO_ID) or {"provider": provider}


def _clean_single_token(value: str | None, *, field: str, max_length: int) -> str:
    value = (value or "").strip()
    if len(value) > max_length:
        raise AppError("invalid_request", f"{field} is too long (max {max_length} characters).")
    if any(ch.isspace() for ch in value):
        # Model IDs and API keys never contain spaces -- almost always a paste error.
        raise AppError("invalid_request", f"{field} must not contain spaces.")
    return value


def _key_status(doc: dict) -> str:
    """One of: none, saved, unreadable (can't decrypt), unknown (encryption key unavailable)."""
    if not doc.get("api_key_encrypted"):
        return "none"
    try:
        credentials_crypto.decrypt(doc["api_key_encrypted"])
    except StoredKeyUnreadable:
        return "unreadable"
    except EncryptionKeyUnavailable:
        return "unknown"
    return "saved"


def _view(doc: dict) -> dict:
    meta = PROVIDERS[doc["provider"]]
    key_status = _key_status(doc)
    return {
        "provider": doc["provider"],
        "display_name": meta["display_name"],
        "implemented": meta["implemented"],
        "model_example": meta["model_example"],
        "model": doc.get("model"),
        "key_status": key_status,
        "key_updated_at": doc.get("key_updated_at"),
        "active": bool(doc.get("active")),
        "configured": meta["implemented"] and key_status == "saved" and bool(doc.get("model")),
        "last_tested_at": doc.get("last_tested_at"),
        "last_test_result": doc.get("last_test_result"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def list_providers() -> list[dict]:
    docs = {doc["provider"]: doc for doc in get_db().ai_providers.find({}, _NO_ID)}
    return [_view(docs.get(provider, {"provider": provider})) for provider in PROVIDERS]


def get_provider(provider: str) -> dict:
    _require_provider(provider, implemented=False)
    return _view(_get_doc(provider))


def get_active() -> dict | None:
    """The raw active document (including ciphertext) -- for the client factory only."""
    return get_db().ai_providers.find_one({"active": True}, _NO_ID)


def get_active_summary() -> dict | None:
    """Provider + model name of the active provider, for display. Never includes the key."""
    doc = get_active()
    if doc is None:
        return None
    return {"provider": doc["provider"], "display_name": PROVIDERS[doc["provider"]]["display_name"], "model": doc.get("model")}


def decrypt_api_key(doc: dict) -> str | None:
    """Plaintext API key for `doc`, or None if none is saved.

    Raises EncryptionKeyUnavailable / StoredKeyUnreadable. The result must
    only ever be handed straight to a provider SDK -- never logged or rendered.
    """
    if not doc.get("api_key_encrypted"):
        return None
    return credentials_crypto.decrypt(doc["api_key_encrypted"])


def active_provider_ready() -> bool:
    """For /healthz: an active provider exists and its key can be decrypted. Never raises."""
    try:
        doc = get_active()
        return bool(doc and doc.get("model") and decrypt_api_key(doc))
    except Exception:  # noqa: BLE001 -- a health check must never raise
        return False


def credentials_for_test(provider: str, *, api_key: str | None, model: str | None) -> tuple[str, str]:
    """Key + model a connection test should use: the typed values, else the saved ones."""
    _require_provider(provider)
    api_key = _clean_single_token(api_key, field="API key", max_length=API_KEY_MAX_LENGTH)
    model = _clean_single_token(model, field="Model", max_length=MODEL_MAX_LENGTH)
    doc = _get_doc(provider)
    model = model or doc.get("model")
    if not model:
        raise AppError("invalid_request", "Enter a model name to test.")
    if not api_key:
        try:
            api_key = decrypt_api_key(doc)
        except EncryptionKeyUnavailable:
            raise AppError("encryption_key_unavailable", _ENCRYPTION_KEY_MESSAGE, 503) from None
        except StoredKeyUnreadable:
            raise AppError("stored_key_unreadable", "The saved API key can't be read. Enter the key again.") from None
    if not api_key:
        raise AppError("invalid_request", "Enter an API key to test.")
    return api_key, model


def save_credentials(provider: str, *, api_key: str | None, model: str, updated_by: str, test_result: str | None = None) -> dict:
    """Save a provider's model, and its API key if one was entered.

    A blank api_key keeps the stored key unchanged. Pass test_result when
    the caller already tested exactly these values; otherwise any earlier
    result is cleared if the key or model changed, since it no longer applies.
    """
    _require_provider(provider)
    api_key = _clean_single_token(api_key, field="API key", max_length=API_KEY_MAX_LENGTH)
    model = _clean_single_token(model, field="Model", max_length=MODEL_MAX_LENGTH)
    if not model:
        raise AppError("invalid_request", "Model is required.")

    doc = _get_doc(provider)
    now = datetime.now(timezone.utc)
    update = {"model": model, "updated_at": now, "updated_by": updated_by}
    if api_key:
        try:
            update["api_key_encrypted"] = credentials_crypto.encrypt(api_key)
        except EncryptionKeyUnavailable:
            # Never fall back to storing the key in plaintext (Appendix A.1).
            raise AppError("encryption_key_unavailable", _ENCRYPTION_KEY_MESSAGE, 503) from None
        update["key_updated_at"] = now

    if test_result is not None:
        update["last_test_result"] = test_result
        update["last_tested_at"] = now
    elif api_key or model != doc.get("model"):
        update["last_test_result"] = None
        update["last_tested_at"] = None

    get_db().ai_providers.update_one({"provider": provider}, {"$set": update}, upsert=True)
    return get_provider(provider)


def record_test_result(provider: str, ok: bool) -> None:
    _require_provider(provider)
    get_db().ai_providers.update_one(
        {"provider": provider},
        {"$set": {"last_test_result": TEST_OK if ok else TEST_FAILED, "last_tested_at": datetime.now(timezone.utc)}},
    )


def remove_key(provider: str, *, updated_by: str) -> None:
    _require_provider(provider, implemented=False)
    if _get_doc(provider).get("active"):
        raise AppError(
            "provider_active", "This provider is active. Make another provider active before removing its key.", 409
        )
    get_db().ai_providers.update_one(
        {"provider": provider},
        {
            "$set": {
                "api_key_encrypted": None,
                "key_updated_at": None,
                "last_test_result": None,
                "last_tested_at": None,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": updated_by,
            }
        },
    )


def set_active(provider: str, *, updated_by: str) -> None:
    """Make `provider` the one active provider. It must have a readable key and a model."""
    meta = _require_provider(provider)
    view = _view(_get_doc(provider))
    if view["key_status"] == "unknown":
        raise AppError("encryption_key_unavailable", _ENCRYPTION_KEY_MESSAGE, 503)
    if not view["configured"]:
        raise AppError(
            "provider_not_configured", f"Save a working API key and model for {meta['display_name']} first.", 400
        )

    db = get_db()
    now = datetime.now(timezone.utc)
    # Turn the new one on before turning the others off: a chat message that
    # lands in between sees two configured providers (either works), never zero.
    db.ai_providers.update_one(
        {"provider": provider}, {"$set": {"active": True, "updated_at": now, "updated_by": updated_by}}
    )
    db.ai_providers.update_many(
        {"provider": {"$ne": provider}, "active": True},
        {"$set": {"active": False, "updated_at": now, "updated_by": updated_by}},
    )
