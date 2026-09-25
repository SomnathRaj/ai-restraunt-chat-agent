import pytest

from app.ai.agent import ChatAgent
from app.ai.conversation import LLMTurn
from app.ai.providers import registry
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable
from app.ai.providers.gemini import GeminiProviderClient
from app.services import ai_provider_service
from tests.conftest import activate_provider


class _ScriptedAdapter:
    """Stands in for a real adapter class in ADAPTERS; records how it was built."""

    built = []

    def __init__(self, api_key, model, reply="ok", exc=None):
        self.api_key, self.model, self._reply, self._exc = api_key, model, reply, exc
        _ScriptedAdapter.built.append((api_key, model))

    tools_seen = []

    def generate(self, history, tools, system_instruction):
        _ScriptedAdapter.tools_seen.append(tools)
        if self._exc:
            raise self._exc
        return LLMTurn(text=f"{self._reply} from {self.model}")


@pytest.fixture(autouse=True)
def _reset_built():
    _ScriptedAdapter.built = []
    _ScriptedAdapter.tools_seen = []


def test_adapters_match_the_providers_marked_implemented():
    implemented = {p for p, meta in ai_provider_service.PROVIDERS.items() if meta["implemented"]}
    assert set(registry.ADAPTERS) == implemented


def test_no_active_provider_raises_not_configured(app):
    with pytest.raises(AIProviderNotConfigured):
        registry.get_active_ai_client()


def test_active_gemini_builds_the_adapter_with_the_decrypted_key_and_model(app):
    activate_provider("gemini", api_key="sk-stored", model="gemini-x")
    client = registry.get_active_ai_client()
    assert isinstance(client, GeminiProviderClient)
    assert client._api_key == "sk-stored"
    assert client._model == "gemini-x"


@pytest.mark.parametrize("encryption_key", [None, "garbage", "7Z9vTDPOyI0OVqXnq9yL8fqg8Hc8ZbL2mD1VYzY0G5Q="])
def test_unreadable_credentials_make_the_provider_unavailable(app, encryption_key):
    activate_provider("gemini")
    app.config["AI_CREDENTIALS_ENCRYPTION_KEY"] = encryption_key
    with pytest.raises(AIProviderUnavailable):
        registry.get_active_ai_client()


def test_check_connection_reports_success(app, monkeypatch):
    monkeypatch.setitem(registry.ADAPTERS, "gemini", _ScriptedAdapter)
    ok, message = registry.check_connection("gemini", api_key="typed", model="gemini-x")
    assert ok is True
    assert "gemini-x" in message
    assert _ScriptedAdapter.built == [("typed", "gemini-x")]
    # The real tool schemas are sent, so a model that can't do tool calling fails the test.
    from app.ai.tool_schemas import TOOL_SCHEMAS

    assert _ScriptedAdapter.tools_seen == [TOOL_SCHEMAS]


def test_check_connection_passes_when_the_model_calls_a_tool(app, monkeypatch):
    from app.ai.conversation import ToolCall

    monkeypatch.setitem(registry.ADAPTERS, "gemini", _ScriptedAdapter)
    monkeypatch.setattr(
        _ScriptedAdapter, "generate", lambda self, *a, **k: LLMTurn(function_calls=[ToolCall(name="get_cart", args={})])
    )
    ok, _ = registry.check_connection("gemini", api_key="k", model="m")
    assert ok is True


@pytest.mark.parametrize(
    ("exc", "phrase"),
    [
        (AIProviderUnavailable("Gemini API error 404"), "Gemini API error 404"),
        (AIProviderRateLimited("Gemini rate limit exceeded"), "rate-limiting"),
    ],
)
def test_check_connection_reports_failure_without_raising(app, monkeypatch, exc, phrase):
    monkeypatch.setitem(registry.ADAPTERS, "gemini", lambda api_key, model: _ScriptedAdapter(api_key, model, exc=exc))
    ok, message = registry.check_connection("gemini", api_key="sk-typed-secret", model="m")
    assert ok is False
    assert phrase in message
    assert "sk-typed-secret" not in message


def test_check_connection_treats_an_empty_reply_as_failure(app, monkeypatch):
    monkeypatch.setitem(registry.ADAPTERS, "gemini", _ScriptedAdapter)
    monkeypatch.setattr(_ScriptedAdapter, "generate", lambda self, *a, **k: LLMTurn(text="  "))
    ok, _ = registry.check_connection("gemini", api_key="k", model="m")
    assert ok is False


def test_provider_switch_applies_on_the_very_next_message(app, monkeypatch):
    # MULTI_AI_PROVIDER_DESIGN.md Section 6 / D5: no caching, no restart.
    monkeypatch.setitem(registry.ADAPTERS, "gemini", _ScriptedAdapter)
    monkeypatch.setitem(registry.ADAPTERS, "openai", _ScriptedAdapter)
    activate_provider("gemini", model="gemini-x")
    activate_provider("openai", model="gpt-x")
    ai_provider_service.set_active("gemini", updated_by="usr_test")

    agent = ChatAgent()
    assert agent.handle_message("s1", "hi") == "ok from gemini-x"
    ai_provider_service.set_active("openai", updated_by="usr_test")
    assert agent.handle_message("s1", "hi again") == "ok from gpt-x"


def test_active_openai_builds_the_openai_adapter(app):
    from app.ai.providers.openai_provider import OpenAIProviderClient

    activate_provider("openai", api_key="sk-openai", model="gpt-x")
    client = registry.get_active_ai_client()
    assert isinstance(client, OpenAIProviderClient)
    assert (client._api_key, client._model) == ("sk-openai", "gpt-x")


def test_active_anthropic_builds_the_anthropic_adapter(app):
    from app.ai.providers.anthropic_provider import AnthropicProviderClient

    activate_provider("anthropic", api_key="sk-ant-stored", model="claude-x")
    client = registry.get_active_ai_client()
    assert isinstance(client, AnthropicProviderClient)
    assert (client._api_key, client._model) == ("sk-ant-stored", "claude-x")


def test_active_openrouter_builds_the_openrouter_adapter(app):
    from app.ai.providers.openrouter_provider import OpenRouterProviderClient

    activate_provider("openrouter", api_key="sk-or-stored", model="openai/gpt-x")
    client = registry.get_active_ai_client()
    assert isinstance(client, OpenRouterProviderClient)
    assert (client._api_key, client._model) == ("sk-or-stored", "openai/gpt-x")


def _counting_sdk_constructor(monkeypatch, provider):
    """Replace the provider's real SDK client class with one that counts constructions.

    Every call on the fake raises that SDK's own connection error, which the
    adapter maps to AIProviderUnavailable -- so no network is ever touched.
    """
    import httpx
    import httpx2
    from types import SimpleNamespace

    import anthropic as anthropic_sdk
    import openai as openai_sdk

    from app.ai.providers import anthropic_provider, gemini, openai_provider

    built = []
    request = httpx2.Request("POST", "https://example.invalid")

    def fail(**kwargs):
        raise {
            "gemini": httpx.ConnectError("offline"),
            "anthropic": anthropic_sdk.APIConnectionError(request=request),
        }.get(provider) or openai_sdk.APIConnectionError(request=request)

    def construct(**kwargs):
        built.append(kwargs.get("api_key"))
        return SimpleNamespace(
            models=SimpleNamespace(generate_content=fail),
            chat=SimpleNamespace(completions=SimpleNamespace(create=fail)),
            messages=SimpleNamespace(create=fail),
        )

    target = {"gemini": (gemini.genai, "Client"), "anthropic": (anthropic_provider.anthropic, "Anthropic")}.get(
        provider, (openai_provider.openai, "OpenAI")
    )
    monkeypatch.setattr(*target, construct)
    return built


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic", "openrouter"])
def test_sdk_client_is_built_once_per_message_and_fresh_for_the_next(app, monkeypatch, provider):
    from app.ai.conversation import UserMessage

    built = _counting_sdk_constructor(monkeypatch, provider)
    activate_provider(provider, api_key=f"key-{provider}", model="m")

    # One customer message = one adapter; its round-trips share one SDK client.
    adapter = registry.get_active_ai_client()
    for _ in range(3):
        with pytest.raises(AIProviderUnavailable):
            adapter.generate([UserMessage(text="hi")], tools=[], system_instruction="")
    assert built == [f"key-{provider}"]

    # The next message resolves a fresh adapter, so a new client (and the
    # current key) -- an admin switch or key change still applies at once.
    with pytest.raises(AIProviderUnavailable):
        registry.get_active_ai_client().generate([UserMessage(text="hi")], tools=[], system_instruction="")
    assert built == [f"key-{provider}", f"key-{provider}"]
