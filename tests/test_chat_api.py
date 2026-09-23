"""Route-level tests for POST /api/chat (PRD Section 44-45).

These specifically cover Phase 9 hardening: graceful handling of real
Gemini API failures (quota/rate-limit, server errors, network timeouts)
that must never surface as a raw 500 or leak SDK internals to the client.
"""

from types import SimpleNamespace

import httpx
from google.genai.errors import APIError

from app.ai.providers import gemini
from tests.conftest import activate_provider


def _use_sdk_that_raises(monkeypatch, exc):
    """Make Gemini the active provider and swap its SDK client for one whose every call raises exc.

    Exercises the full real path -- route -> ChatAgent -> registry (decrypting
    the stored key) -> GeminiProviderClient's error mapping -- with zero
    network access. Returns the list of API keys the SDK client was built with.
    """
    activate_provider("gemini", api_key="stored-gemini-key")
    built_with = []

    def generate_content(**kwargs):
        raise exc

    def fake_client(api_key):
        built_with.append(api_key)
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

    monkeypatch.setattr(gemini.genai, "Client", fake_client)
    return built_with


def test_chat_requires_session_id(client):
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_chat_rejects_empty_message(client):
    response = client.post("/api/chat", json={"session_id": "s1", "message": "   "})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_chat_handles_gemini_rate_limit_gracefully(client, monkeypatch):
    _use_sdk_that_raises(monkeypatch, APIError(429, {"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}}))

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 429
    data = response.get_json()
    assert data["error"] == "ai_rate_limited"
    # Never leak SDK internals (status name, raw response body) to the customer.
    assert "RESOURCE_EXHAUSTED" not in data["message"]


def test_chat_handles_gemini_server_error_gracefully(client, monkeypatch):
    _use_sdk_that_raises(monkeypatch, APIError(500, {"error": {"message": "internal error"}}))

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"


def test_chat_handles_network_timeout_gracefully(client, monkeypatch):
    _use_sdk_that_raises(monkeypatch, httpx.ReadTimeout("timed out"))

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"


def test_chat_handles_deprecated_model_gracefully(client, monkeypatch):
    # The real incident behind the "Test connection" requirement: a pinned,
    # since-deprecated model name returns 404 -- must surface as a friendly 503.
    _use_sdk_that_raises(monkeypatch, APIError(404, {"error": {"message": "model not found", "status": "NOT_FOUND"}}))

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"


def test_chat_reports_not_configured_when_no_provider_is_active(client):
    # Fresh install: the four provider documents exist, none active.
    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    data = response.get_json()
    assert data["error"] == "ai_not_configured"
    # Customer-facing: no env var names or provider internals.
    assert "GEMINI" not in data["message"] and ".env" not in data["message"]


def test_chat_uses_the_decrypted_stored_key(client, monkeypatch):
    built_with = _use_sdk_that_raises(monkeypatch, APIError(500, {"error": {"message": "internal"}}))

    client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert built_with == ["stored-gemini-key"]


def test_chat_is_unavailable_when_the_stored_key_cant_be_decrypted(app, client, monkeypatch):
    # Appendix A.1: the encryption key changed after the provider key was saved.
    _use_sdk_that_raises(monkeypatch, AssertionError("SDK must not be called"))
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = "7Z9vTDPOyI0OVqXnq9yL8fqg8Hc8ZbL2mD1VYzY0G5Q="

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"


def test_chat_is_unavailable_when_the_encryption_key_is_missing(app, client, monkeypatch):
    _use_sdk_that_raises(monkeypatch, AssertionError("SDK must not be called"))
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = None

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"


def test_chat_rate_limit_enforced_below_gemini_free_tier(client, monkeypatch):
    # 10/minute is deliberately tighter than Gemini's own free-tier quota
    # (15/minute) -- our limiter should trip first for a fast-typing
    # customer, giving a friendly 429 rather than a surprise failure from
    # Google mid-conversation. Avoid burning real Gemini calls -- script a
    # trivial no-tool reply via a fake ChatAgent.
    from tests.conftest import FakeAIClient, LLMTurn

    fake = FakeAIClient([LLMTurn(text="ok")] * 11)
    monkeypatch.setattr("app.api.chat.ChatAgent", lambda: _FakeAgent(fake))

    statuses = [client.post("/api/chat", json={"session_id": "s1", "message": f"msg {i}"}).status_code for i in range(11)]

    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


class _FakeAgent:
    def __init__(self, fake_client):
        self._fake_client = fake_client

    def handle_message(self, session_id, message):
        turn = self._fake_client.generate([], tools=[], system_instruction="")
        return turn.text
