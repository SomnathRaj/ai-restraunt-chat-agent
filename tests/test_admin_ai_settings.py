"""Admin AI Settings page (MULTI_AI_PROVIDER_DESIGN.md Section 5).

registry.check_connection is replaced with a scripted stand-in -- no network.
"""

import pytest
from werkzeug.security import generate_password_hash

from app.ai.providers import registry
from app.models.db import get_db
from app.services import ai_provider_service as svc
from tests.conftest import activate_provider

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"
SECRET = "sk-live-VERY-SECRET-123"
URL = "/admin/settings/ai"


@pytest.fixture
def admin_client(app, client):
    get_db().users.insert_one(
        {
            "user_id": "usr_test",
            "email": ADMIN_EMAIL,
            "password_hash": generate_password_hash(ADMIN_PASSWORD),
            "name": "Admin",
            "role": "admin",
            "active": True,
        }
    )
    client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return client


@pytest.fixture
def connection(monkeypatch):
    """Scripted check_connection: set .ok to control the outcome; .calls records (provider, key, model)."""

    class _Connection:
        ok = True
        calls = []

        def __call__(self, provider, *, api_key, model):
            self.calls.append((provider, api_key, model))
            return (True, f"Connected. {model} replied.") if self.ok else (False, "Couldn't get a reply. (Gemini API error 404)")

    fake = _Connection()
    fake.calls = []
    monkeypatch.setattr(registry, "check_connection", fake)
    return fake


def _raw(provider="gemini"):
    return get_db().ai_providers.find_one({"provider": provider}, {"_id": 0})


def _save(client, provider="gemini", **form):
    return client.post(f"{URL}/{provider}", data={"action": "save", **form}, follow_redirects=True)


# ---------------------------------------------------------------------------
# Access + rendering
# ---------------------------------------------------------------------------


def test_requires_login(client):
    response = client.get(URL)
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]
    assert client.post(f"{URL}/gemini", data={"model": "m"}).status_code == 302


def test_page_lists_all_four_providers_and_nav_link(admin_client):
    body = admin_client.get(URL).get_data(as_text=True)
    for name in ("Google Gemini", "OpenAI", "Anthropic Claude", "OpenRouter"):
        assert name in body
    assert "Available in a later release" not in body  # all four have adapters
    for provider in ("gemini", "openai", "anthropic", "openrouter"):
        assert f'id="api_key-{provider}"' in body
    assert "No AI provider is active" in body
    assert 'href="/admin/settings/ai"' in admin_client.get("/admin/").get_data(as_text=True)


def test_saved_key_is_never_rendered_back(admin_client, connection):
    _save(admin_client, api_key=SECRET, model="gemini-x")
    ciphertext = _raw()["api_key_encrypted"]
    for page in ("/admin/", URL):
        body = admin_client.get(page).get_data(as_text=True)
        assert SECRET not in body
        assert ciphertext not in body
    assert "saved" in admin_client.get(URL).get_data(as_text=True)


def test_encryption_key_banner_when_missing(app, admin_client):
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = None
    assert "AI_CREDENTIALS_ENCRYPTION_KEY is not set" in admin_client.get(URL).get_data(as_text=True)


def test_encryption_key_banner_when_malformed_explains_the_format(app, admin_client):
    # A 64-char hex secret (e.g. secrets.token_hex(32)) is a common mistake.
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = "ab" * 32
    body = admin_client.get(URL).get_data(as_text=True)
    assert "is not a valid key" in body
    assert "Fernet key: 44 characters" in body
    assert "Fernet.generate_key()" in body
    assert "ab" * 32 not in body


def test_unreadable_stored_key_is_flagged_for_re_entry(app, admin_client, connection):
    _save(admin_client, api_key=SECRET, model="gemini-x")
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = "7Z9vTDPOyI0OVqXnq9yL8fqg8Hc8ZbL2mD1VYzY0G5Q="
    assert "Stored key unreadable — re-enter" in admin_client.get(URL).get_data(as_text=True)


def test_dashboard_shows_active_provider_and_model_but_not_the_key(admin_client):
    assert "no provider is active" in admin_client.get("/admin/").get_data(as_text=True)
    activate_provider("gemini", api_key=SECRET, model="gemini-x")
    body = admin_client.get("/admin/").get_data(as_text=True)
    assert "Google Gemini" in body and "gemini-x" in body
    assert SECRET not in body


# ---------------------------------------------------------------------------
# Save / test
# ---------------------------------------------------------------------------


def test_save_on_inactive_provider_encrypts_then_auto_tests(admin_client, connection):
    response = _save(admin_client, api_key=SECRET, model="gemini-x")
    assert response.status_code == 200
    raw = _raw()
    assert svc.decrypt_api_key(raw) == SECRET
    assert raw["model"] == "gemini-x"
    assert raw["updated_by"] == "usr_test"
    assert raw["active"] is False  # saving never activates
    assert connection.calls == [("gemini", SECRET, "gemini-x")]
    assert raw["last_test_result"] == svc.TEST_OK
    assert "Saved." in response.get_data(as_text=True)


def test_save_on_inactive_provider_records_a_failed_test(admin_client, connection):
    connection.ok = False
    response = _save(admin_client, api_key=SECRET, model="gemini-typo")
    assert _raw()["model"] == "gemini-typo"  # still saved -- nobody is using it yet
    assert _raw()["last_test_result"] == svc.TEST_FAILED
    assert "connection test failed" in response.get_data(as_text=True)


def test_blank_key_keeps_the_saved_key(admin_client, connection):
    _save(admin_client, api_key=SECRET, model="gemini-x")
    _save(admin_client, api_key="", model="gemini-y")
    assert svc.decrypt_api_key(_raw()) == SECRET
    assert _raw()["model"] == "gemini-y"


def test_model_only_save_skips_the_test(admin_client, connection):
    response = _save(admin_client, api_key="", model="gemini-x")
    assert connection.calls == []
    assert "Add an API key" in response.get_data(as_text=True)


def test_invalid_model_is_rejected_with_the_typed_value_kept(admin_client, connection):
    response = _save(admin_client, api_key=SECRET, model="has space")
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "must not contain spaces" in body
    assert 'value="has space"' in body
    assert SECRET not in body
    assert _raw()["api_key_encrypted"] is None


def test_save_on_active_provider_tests_first_and_saves_nothing_on_failure(admin_client, connection):
    activate_provider("gemini", api_key="sk-old", model="gemini-good")
    connection.ok = False
    response = _save(admin_client, api_key="sk-new-typo", model="gemini-typo")
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Not saved" in body
    assert 'name="save_anyway"' in body
    assert "sk-new-typo" not in body
    assert _raw()["model"] == "gemini-good"
    assert svc.decrypt_api_key(_raw()) == "sk-old"


def test_save_anyway_on_active_provider_saves_and_records_failure(admin_client, connection):
    activate_provider("gemini", api_key="sk-old", model="gemini-good")
    connection.ok = False
    _save(admin_client, api_key="", model="gemini-new", save_anyway="1")
    assert _raw()["model"] == "gemini-new"
    assert _raw()["last_test_result"] == svc.TEST_FAILED


def test_save_on_active_provider_saves_when_test_passes(admin_client, connection):
    activate_provider("gemini", api_key="sk-old", model="gemini-good")
    _save(admin_client, api_key="sk-new", model="gemini-better")
    assert connection.calls == [("gemini", "sk-new", "gemini-better")]
    assert svc.decrypt_api_key(_raw()) == "sk-new"
    assert _raw()["last_test_result"] == svc.TEST_OK


def test_test_connection_on_saved_values_records_the_result(admin_client, connection):
    _save(admin_client, api_key=SECRET, model="gemini-x")
    connection.ok = False
    connection.calls = []
    admin_client.post(f"{URL}/gemini", data={"action": "test", "api_key": "", "model": "gemini-x"})
    assert connection.calls == [("gemini", SECRET, "gemini-x")]
    assert _raw()["last_test_result"] == svc.TEST_FAILED


def test_test_connection_with_typed_values_saves_nothing(admin_client, connection):
    _save(admin_client, api_key=SECRET, model="gemini-x")
    before = _raw()
    response = admin_client.post(
        f"{URL}/gemini", data={"action": "test", "api_key": "sk-other", "model": "gemini-y"}, follow_redirects=True
    )
    assert connection.calls[-1] == ("gemini", "sk-other", "gemini-y")
    assert _raw() == before
    body = response.get_data(as_text=True)
    assert "Nothing was saved yet" in body
    assert "sk-other" not in body


def test_provider_without_an_adapter_is_shown_but_cannot_be_saved(admin_client, connection, monkeypatch):
    monkeypatch.setitem(svc.PROVIDERS["openrouter"], "implemented", False)
    assert "Available in a later release" in admin_client.get(URL).get_data(as_text=True)
    response = _save(admin_client, "openrouter", api_key=SECRET, model="openai/gpt-x")
    assert response.status_code == 400
    assert _raw("openrouter")["api_key_encrypted"] is None


def test_unknown_provider_is_404(admin_client, connection):
    assert _save(admin_client, "azure", api_key="k", model="m").status_code == 404


# ---------------------------------------------------------------------------
# Active provider
# ---------------------------------------------------------------------------


def test_unconfigured_provider_cannot_be_made_active(admin_client):
    response = admin_client.post(f"{URL}/active", data={"provider": "gemini"})
    assert response.status_code == 400
    assert svc.get_active() is None


def test_unimplemented_provider_cannot_be_made_active(admin_client, monkeypatch):
    # Fully configured and tested, so the ONLY reason to refuse is the missing adapter.
    svc.save_credentials("openrouter", api_key="sk-or", model="openai/gpt-x", updated_by="usr_test", test_result=svc.TEST_OK)
    activate_provider("gemini")
    monkeypatch.setitem(svc.PROVIDERS["openrouter"], "implemented", False)

    response = admin_client.post(f"{URL}/active", data={"provider": "openrouter"})

    assert response.status_code == 400
    assert "OpenRouter isn&#39;t available yet" in response.get_data(as_text=True)  # HTML-escaped
    assert svc.get_active()["provider"] == "gemini"


def test_tested_provider_can_be_made_active(admin_client, connection):
    _save(admin_client, api_key=SECRET, model="gemini-x")
    response = admin_client.post(f"{URL}/active", data={"provider": "gemini"}, follow_redirects=True)
    assert "is now the active AI provider" in response.get_data(as_text=True)
    assert svc.get_active()["provider"] == "gemini"


def test_untested_provider_needs_activate_anyway(admin_client, connection):
    connection.ok = False
    _save(admin_client, api_key=SECRET, model="gemini-x")

    response = admin_client.post(f"{URL}/active", data={"provider": "gemini"})
    assert response.status_code == 400
    assert 'name="confirm_untested"' in response.get_data(as_text=True)
    assert svc.get_active() is None

    admin_client.post(f"{URL}/active", data={"provider": "gemini", "confirm_untested": "1"})
    assert svc.get_active()["provider"] == "gemini"


# ---------------------------------------------------------------------------
# Remove key
# ---------------------------------------------------------------------------


def test_remove_key_is_blocked_for_the_active_provider(admin_client):
    activate_provider("gemini")
    response = admin_client.post(f"{URL}/gemini/remove-key")
    assert response.status_code == 409
    assert _raw()["api_key_encrypted"] is not None


def test_remove_key_on_inactive_provider(admin_client, connection):
    _save(admin_client, api_key=SECRET, model="gemini-x")
    response = admin_client.post(f"{URL}/gemini/remove-key", follow_redirects=True)
    assert "API key removed" in response.get_data(as_text=True)
    assert _raw()["api_key_encrypted"] is None
