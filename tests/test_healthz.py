def test_healthz_reports_mongo_true_and_gemini_false(client):
    """Proves the app boots and /healthz reflects configuration state.

    mongodb is True here because the conftest `app` fixture injects a
    mongomock client. gemini is False because GEMINI_API_KEY is unset in
    TestConfig -- matches how the app behaves before real credentials are
    supplied (ARCHITECTURE.md Section 7).
    """
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"mongodb": True, "gemini": False}


def test_index_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Welcome" in response.data
