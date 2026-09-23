from tests.conftest import activate_provider


def test_healthz_reports_mongo_true_and_ai_false_on_a_fresh_install(client):
    """Proves the app boots and /healthz reflects configuration state.

    mongodb is True here because the conftest `app` fixture injects a
    mongomock client. ai is False because no provider has been made active
    in Admin -> AI Settings yet (ARCHITECTURE.md Section 7).
    """
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"mongodb": True, "ai": False}


def test_healthz_reports_ai_true_once_a_provider_is_active(client):
    activate_provider("gemini")
    assert client.get("/healthz").get_json() == {"mongodb": True, "ai": True}


def test_healthz_reports_ai_false_when_the_stored_key_cant_be_decrypted(app, client):
    activate_provider("gemini")
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = None
    assert client.get("/healthz").get_json() == {"mongodb": True, "ai": False}


def test_index_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Welcome" in response.data
