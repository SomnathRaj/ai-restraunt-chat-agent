"""Route-level tests for POST /api/chat (PRD Section 44-45).

These specifically cover Phase 9 hardening: graceful handling of real
Gemini API failures (quota/rate-limit, server errors, network timeouts)
that must never surface as a raw 500 or leak SDK internals to the client.
"""

import httpx
from google.genai.errors import APIError

from app.ai.gemini_client import GeminiClient


def test_chat_requires_session_id(client):
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_chat_rejects_empty_message(client):
    response = client.post("/api/chat", json={"session_id": "s1", "message": "   "})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_chat_handles_gemini_rate_limit_gracefully(client, monkeypatch):
    def raise_429(self, contents, tools, system_instruction):
        raise APIError(429, {"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}})

    monkeypatch.setattr(GeminiClient, "generate", raise_429)

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 429
    data = response.get_json()
    assert data["error"] == "ai_rate_limited"
    # Never leak SDK internals (status name, raw response body) to the customer.
    assert "RESOURCE_EXHAUSTED" not in data["message"]


def test_chat_handles_gemini_server_error_gracefully(client, monkeypatch):
    def raise_500(self, contents, tools, system_instruction):
        raise APIError(500, {"error": {"message": "internal error"}})

    monkeypatch.setattr(GeminiClient, "generate", raise_500)

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"


def test_chat_handles_network_timeout_gracefully(client, monkeypatch):
    def raise_timeout(self, contents, tools, system_instruction):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(GeminiClient, "generate", raise_timeout)

    response = client.post("/api/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"


def test_chat_rate_limit_enforced_below_gemini_free_tier(client, monkeypatch):
    # 10/minute is deliberately tighter than Gemini's own free-tier quota
    # (15/minute) -- our limiter should trip first for a fast-typing
    # customer, giving a friendly 429 rather than a surprise failure from
    # Google mid-conversation. Avoid burning real Gemini calls -- script a
    # trivial no-tool reply via a fake ChatAgent.
    from tests.conftest import FakeGeminiClient, LLMTurn

    fake = FakeGeminiClient([LLMTurn(text="ok")] * 11)
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
