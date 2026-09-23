"""Provider-independent guarantees (MULTI_AI_PROVIDER_DESIGN.md Section 2, Final checklist).

The AI never determines price, availability, totals, or order IDs -- no matter
which provider is active -- because (1) no tool can even accept such a value,
(2) no adapter can run a tool or reach a service itself, and (3) every tool
call goes through ToolExecutor's allow-list into the services, which read
prices from MongoDB and compute totals/order IDs server-side.
"""

import ast
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from app.ai.agent import ChatAgent
from app.ai.conversation import LLMTurn
from app.ai.providers import registry
from app.ai.tool_schemas import TOOL_SCHEMAS
from app.models.db import get_db
from app.services import ai_provider_service, cart_service
from tests.conftest import FakeAIClient, ToolCall

ADAPTER_MODULES = ["gemini", "openai_provider", "anthropic_provider", "openrouter_provider"]
PROVIDERS_DIR = Path(__file__).resolve().parent.parent / "app" / "ai" / "providers"

# Values the backend alone decides. get_order_status's order_id is a lookup
# key for an existing order, not a value the model sets.
FORBIDDEN_PARAMETERS = {"price", "unit_price", "total", "subtotal", "amount", "discount", "availability", "available", "order_id"}
LOOKUP_ONLY = {("get_order_status", "order_id")}


def test_no_tool_accepts_a_value_the_backend_decides():
    for schema in TOOL_SCHEMAS:
        for param in schema["parameters"].get("properties", {}):
            if (schema["name"], param) in LOOKUP_ONLY:
                continue
            assert param not in FORBIDDEN_PARAMETERS, f"{schema['name']} accepts {param!r}"


@pytest.mark.parametrize("module", ADAPTER_MODULES)
def test_adapters_never_import_services_or_the_tool_executor(module):
    tree = ast.parse((PROVIDERS_DIR / f"{module}.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not any(name.startswith("app.services") or name == "app.ai.tool_executor" for name in imported), imported


def test_a_model_supplied_price_is_ignored(app):
    get_db().menu.insert_one(
        {
            "item_id": "coke",
            "name": "Coke",
            "description": "",
            "category": "Beverage",
            "price": 60,
            "availability": True,
            "tags": [],
            "active": True,
        }
    )
    fake = FakeAIClient(
        [
            LLMTurn(function_calls=[ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 2, "price": 1, "total": 2})]),
            LLMTurn(text="Done."),
        ]
    )
    ChatAgent(client=fake).handle_message("s1", "Two cokes at 1 rupee each")
    cart = cart_service.get_cart("s1")
    assert cart["items"][0]["price"] == 60
    assert cart["total"] == 120


# ---------------------------------------------------------------------------
# Switching through all four providers from the admin page, no restart
# ---------------------------------------------------------------------------


class _EchoAdapter:
    """Stands in for every real adapter: replies with which key/model built it."""

    def __init__(self, api_key, model):
        self._api_key, self._model = api_key, model

    def generate(self, history, tools, system_instruction):
        return LLMTurn(text=f"{self._model} answered with {self._api_key}")


@pytest.fixture
def admin_client(app, client):
    get_db().users.insert_one(
        {
            "user_id": "usr_test",
            "email": "admin@gmail.com",
            "password_hash": generate_password_hash("pass123"),
            "name": "Admin",
            "role": "admin",
            "active": True,
        }
    )
    client.post("/admin/login", data={"email": "admin@gmail.com", "password": "pass123"})
    return client


def test_switching_through_all_four_providers_applies_on_the_next_message(admin_client, monkeypatch):
    for provider in ai_provider_service.PROVIDERS:
        monkeypatch.setitem(registry.ADAPTERS, provider, _EchoAdapter)
    monkeypatch.setattr(registry, "check_connection", lambda provider, *, api_key, model: (True, "ok"))

    for provider in ai_provider_service.PROVIDERS:
        response = admin_client.post(
            f"/admin/settings/ai/{provider}",
            data={"action": "save", "api_key": f"key-{provider}", "model": f"model-{provider}"},
        )
        assert response.status_code == 302, provider

    # Go round twice, so every switch is covered, including back to the first.
    for provider in list(ai_provider_service.PROVIDERS) * 2:
        assert admin_client.post("/admin/settings/ai/active", data={"provider": provider}).status_code == 302
        reply = admin_client.post("/api/chat", json={"session_id": "s1", "message": "hi"}).get_json()["reply"]
        assert reply == f"model-{provider} answered with key-{provider}"
        assert [d["provider"] for d in get_db().ai_providers.find({"active": True})] == [provider]
