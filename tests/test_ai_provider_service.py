import pytest

from app.models.db import get_db
from app.services import ai_provider_service as svc
from app.utils.errors import AppError
from tests.conftest import activate_provider

OTHER_KEY = "7Z9vTDPOyI0OVqXnq9yL8fqg8Hc8ZbL2mD1VYzY0G5Q="


@pytest.fixture
def second_provider():
    """A second provider with a real adapter, to exercise switching between two."""
    return "openai"


def _raw(provider):
    return get_db().ai_providers.find_one({"provider": provider}, {"_id": 0})


def test_the_four_fixed_provider_documents_exist_and_start_empty(app):
    docs = list(get_db().ai_providers.find({}, {"_id": 0}))
    assert sorted(d["provider"] for d in docs) == ["anthropic", "gemini", "openai", "openrouter"]
    assert all(d["api_key_encrypted"] is None and d["model"] is None and d["active"] is False for d in docs)


def test_ensure_provider_documents_never_overwrites_saved_values(app):
    svc.save_credentials("gemini", api_key="k1", model="m1", updated_by="usr_test")
    svc.ensure_provider_documents(get_db())
    assert _raw("gemini")["model"] == "m1"
    assert get_db().ai_providers.count_documents({}) == 4


def test_list_providers_is_in_catalog_order_and_never_contains_a_key(app):
    svc.save_credentials("gemini", api_key="sk-secret", model="m1", updated_by="usr_test")
    views = svc.list_providers()
    assert [v["provider"] for v in views] == ["gemini", "openai", "anthropic", "openrouter"]
    flat = repr(views)
    assert "sk-secret" not in flat
    assert "api_key_encrypted" not in flat
    assert _raw("gemini")["api_key_encrypted"] not in flat


def test_save_stores_only_ciphertext(app):
    svc.save_credentials("gemini", api_key="sk-secret", model="gemini-x", updated_by="usr_test")
    raw = _raw("gemini")
    assert raw["api_key_encrypted"] and "sk-secret" not in raw["api_key_encrypted"]
    assert svc.decrypt_api_key(raw) == "sk-secret"
    assert raw["model"] == "gemini-x"
    assert raw["updated_by"] == "usr_test"
    assert raw["key_updated_at"] is not None


def test_blank_key_on_save_keeps_the_existing_key(app):
    svc.save_credentials("gemini", api_key="sk-first", model="m1", updated_by="usr_test")
    svc.save_credentials("gemini", api_key="   ", model="m2", updated_by="usr_test")
    raw = _raw("gemini")
    assert svc.decrypt_api_key(raw) == "sk-first"
    assert raw["model"] == "m2"


def test_changing_key_or_model_clears_an_old_test_result(app):
    svc.save_credentials("gemini", api_key="k", model="m1", updated_by="usr_test", test_result=svc.TEST_OK)
    assert _raw("gemini")["last_test_result"] == svc.TEST_OK

    svc.save_credentials("gemini", api_key="", model="m1", updated_by="usr_test")  # nothing changed
    assert _raw("gemini")["last_test_result"] == svc.TEST_OK

    svc.save_credentials("gemini", api_key="", model="m2", updated_by="usr_test")
    assert _raw("gemini")["last_test_result"] is None


@pytest.mark.parametrize(
    ("api_key", "model", "code"),
    [
        ("k", "", "invalid_request"),
        ("k", "has space", "invalid_request"),
        ("has space", "m", "invalid_request"),
        ("k" * 501, "m", "invalid_request"),
        ("k", "m" * 201, "invalid_request"),
    ],
)
def test_save_validates_inputs(app, api_key, model, code):
    with pytest.raises(AppError) as info:
        svc.save_credentials("gemini", api_key=api_key, model=model, updated_by="usr_test")
    assert info.value.code == code
    assert _raw("gemini")["api_key_encrypted"] is None


def test_unknown_provider_is_rejected(app):
    with pytest.raises(AppError) as info:
        svc.save_credentials("azure", api_key="k", model="m", updated_by="usr_test")
    assert info.value.code == "unknown_provider"
    assert get_db().ai_providers.count_documents({}) == 4


def test_provider_without_an_adapter_cannot_be_saved_or_activated(app, monkeypatch):
    # All four have adapters now; simulate a future catalog entry that doesn't yet.
    monkeypatch.setitem(svc.PROVIDERS["openrouter"], "implemented", False)
    with pytest.raises(AppError) as info:
        svc.save_credentials("openrouter", api_key="k", model="m", updated_by="usr_test")
    assert info.value.code == "provider_not_available"
    with pytest.raises(AppError):
        svc.set_active("openrouter", updated_by="usr_test")


def test_missing_encryption_key_never_falls_back_to_plaintext(app):
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = None
    with pytest.raises(AppError) as info:
        svc.save_credentials("gemini", api_key="sk-secret", model="m", updated_by="usr_test")
    assert info.value.code == "encryption_key_unavailable"
    raw = _raw("gemini")
    assert raw["api_key_encrypted"] is None
    assert "sk-secret" not in repr(raw)


def test_set_active_requires_both_key_and_model(app):
    svc.save_credentials("gemini", api_key="", model="m", updated_by="usr_test")  # model only
    with pytest.raises(AppError) as info:
        svc.set_active("gemini", updated_by="usr_test")
    assert info.value.code == "provider_not_configured"
    assert svc.get_active() is None


def test_set_active_rejects_an_unreadable_key(app):
    svc.save_credentials("gemini", api_key="k", model="m", updated_by="usr_test")
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = OTHER_KEY
    assert svc.get_provider("gemini")["key_status"] == "unreadable"
    with pytest.raises(AppError):
        svc.set_active("gemini", updated_by="usr_test")


def test_exactly_one_provider_is_active_after_switching(app, second_provider):
    activate_provider("gemini")
    activate_provider(second_provider)
    active = [d["provider"] for d in get_db().ai_providers.find({"active": True})]
    assert active == [second_provider]

    svc.set_active("gemini", updated_by="usr_test")
    active = [d["provider"] for d in get_db().ai_providers.find({"active": True})]
    assert active == ["gemini"]


def test_remove_key_is_blocked_for_the_active_provider(app):
    activate_provider("gemini")
    with pytest.raises(AppError) as info:
        svc.remove_key("gemini", updated_by="usr_test")
    assert info.value.code == "provider_active"
    assert _raw("gemini")["api_key_encrypted"] is not None


def test_remove_key_clears_the_key_and_test_result(app, second_provider):
    activate_provider("gemini")
    activate_provider(second_provider)
    svc.remove_key("gemini", updated_by="usr_test")
    raw = _raw("gemini")
    assert raw["api_key_encrypted"] is None
    assert raw["last_test_result"] is None
    assert raw["model"] == "gemini-test-model"  # model is kept
    assert svc.get_provider("gemini")["configured"] is False


def test_active_summary_names_provider_and_model_only(app):
    assert svc.get_active_summary() is None
    activate_provider("gemini", api_key="sk-secret", model="gemini-x")
    assert svc.get_active_summary() == {"provider": "gemini", "display_name": "Google Gemini", "model": "gemini-x"}


def test_credentials_for_test_prefers_typed_values_then_saved_ones(app):
    svc.save_credentials("gemini", api_key="saved-key", model="saved-model", updated_by="usr_test")
    assert svc.credentials_for_test("gemini", api_key="", model="") == ("saved-key", "saved-model")
    assert svc.credentials_for_test("gemini", api_key="typed-key", model="typed-model") == ("typed-key", "typed-model")


def test_credentials_for_test_needs_a_key(app):
    with pytest.raises(AppError):
        svc.credentials_for_test("gemini", api_key="", model="m")


def test_active_provider_ready(app):
    assert svc.active_provider_ready() is False
    activate_provider("gemini")
    assert svc.active_provider_ready() is True
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = "garbage"
    assert svc.active_provider_ready() is False
