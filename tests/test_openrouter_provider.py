"""OpenRouterProviderClient: the OpenAI adapter plus OpenRouter's differences.

The shared Chat Completions translation is covered in test_openai_provider.py;
these tests cover only what OpenRouter changes. Most use a recording fake SDK;
the last runs the REAL openai SDK against a mock OpenRouter HTTP endpoint.
"""

import json
from types import SimpleNamespace

import httpx2
import openai
import pytest

from app.ai.agent import ChatAgent
from app.ai.conversation import UserMessage
from app.ai.providers.base import AIProviderRateLimited, AIProviderUnavailable
from app.ai.providers.openai_provider import OpenAIProviderClient
from app.ai.providers.openrouter_provider import OpenRouterProviderClient
from app.models.db import get_db
from app.services import cart_service

_REQUEST = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
REASONING = [{"type": "reasoning.encrypted", "data": "ENC-1", "id": "r1", "format": "anthropic-claude-v1", "index": 0}]


def _choice(content=None, tool_calls=None, finish_reason="stop", error=None, reasoning_details=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls, refusal=None, reasoning_details=reasoning_details)
    return SimpleNamespace(message=message, finish_reason=finish_reason, error=error)


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments))


class _RecordingSdk:
    def __init__(self, choices=None, exc=None):
        self._choices = list(choices or [])
        self._exc = exc
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(choices=[self._choices.pop(0)])


def _client(sdk):
    return OpenRouterProviderClient(client=sdk, model="anthropic/claude-opus-5")


def test_is_the_openai_adapter_pointed_at_openrouter(app):
    assert issubclass(OpenRouterProviderClient, OpenAIProviderClient)
    real = OpenRouterProviderClient(api_key="sk-or-test", model="openai/gpt-x")._create_client()
    assert str(real.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert (real.timeout, real.max_retries) == (60, 1)


def test_namespaced_model_is_passed_through_and_tool_support_is_required(app):
    sdk = _RecordingSdk([_choice(content="ok")])
    _client(sdk).generate([UserMessage(text="hi")], tools=[], system_instruction="sys")
    request = sdk.requests[0]
    assert request["model"] == "anthropic/claude-opus-5"
    # Without this, tools are only a routing preference and can be silently ignored.
    assert request["extra_body"] == {"provider": {"require_parameters": True}}


def test_plain_openai_sends_no_extra_body(app):
    sdk = _RecordingSdk([_choice(content="ok")])
    OpenAIProviderClient(client=sdk, model="gpt-x").generate([UserMessage(text="hi")], tools=[], system_instruction="")
    assert "extra_body" not in sdk.requests[0]


def test_http_200_with_finish_reason_error_is_unavailable_not_a_reply(app):
    sdk = _RecordingSdk([_choice(content="partial answ", finish_reason="error", error={"code": 502, "message": "upstream died"})])
    with pytest.raises(AIProviderUnavailable) as info:
        _client(sdk).generate([UserMessage(text="hi")], tools=[], system_instruction="")
    assert str(info.value) == "OpenRouter upstream error 502"
    assert "upstream died" not in str(info.value)


def test_reasoning_details_are_kept_only_on_tool_turns(app):
    tool_turn = _client(
        _RecordingSdk([_choice(tool_calls=[_tool_call("c1", "get_cart", "{}")], finish_reason="tool_calls", reasoning_details=REASONING)])
    ).generate([UserMessage(text="cart")], tools=[], system_instruction="")
    assert tool_turn.provider_metadata == {"assistant_extra": {"reasoning_details": REASONING}}

    plain = _client(_RecordingSdk([_choice(content="Hi!", reasoning_details=REASONING)])).generate(
        [UserMessage(text="hi")], tools=[], system_instruction=""
    )
    assert plain.provider_metadata is None


@pytest.mark.parametrize(
    ("exc", "expected", "summary"),
    [
        # OpenRouter's error.code is the numeric status -- must not print "402 402".
        (openai.APIStatusError("no credits", response=httpx2.Response(402, request=_REQUEST), body={"code": 402}), AIProviderUnavailable, "OpenRouter API error 402"),
        (openai.RateLimitError("slow down", response=httpx2.Response(429, request=_REQUEST), body={"code": 429}), AIProviderRateLimited, "OpenRouter rate limit exceeded"),
        (openai.APIStatusError("no provider", response=httpx2.Response(503, request=_REQUEST), body={"code": 503}), AIProviderUnavailable, "OpenRouter API error 503"),
    ],
)
def test_errors_use_the_openrouter_label_and_no_duplicate_numeric_code(app, exc, expected, summary):
    with pytest.raises(expected) as info:
        _client(_RecordingSdk(exc=exc)).generate([UserMessage(text="hi")], tools=[], system_instruction="")
    assert str(info.value) == summary


def test_real_sdk_round_trip_replays_reasoning_details_in_the_tool_loop(app):
    """Real openai SDK + mock OpenRouter endpoint, driven by a real ChatAgent:
    the exact JSON sent must carry require_parameters, and the second call
    must replay reasoning_details on the assistant tool-call message."""
    get_db().menu.insert_one(
        {
            "item_id": "coke",
            "name": "Coke",
            "description": "Chilled soft drink",
            "category": "Beverage",
            "price": 60,
            "availability": True,
            "tags": ["drink"],
            "active": True,
        }
    )
    replies = [
        {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 1,
            "model": "anthropic/claude-opus-5",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_details": REASONING,
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"name": "add_to_cart", "arguments": '{"item_id": "coke", "quantity": 1}'}}
                        ],
                    },
                }
            ],
        },
        {
            "id": "gen-2",
            "object": "chat.completion",
            "created": 1,
            "model": "anthropic/claude-opus-5",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Added a Coke!"}}],
        },
    ]
    sent = []

    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=replies[len(sent) - 1])

    sdk = openai.OpenAI(
        api_key="sk-or-test",
        base_url=OpenRouterProviderClient.BASE_URL,
        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    reply = ChatAgent(client=OpenRouterProviderClient(client=sdk, model="anthropic/claude-opus-5")).handle_message("s1", "One coke")

    assert reply == "Added a Coke!"
    assert cart_service.get_cart("s1")["items"][0]["quantity"] == 1
    assert all(body["provider"] == {"require_parameters": True} for body in sent)
    assistant = next(m for m in sent[1]["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_details"] == REASONING
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert next(m for m in sent[1]["messages"] if m["role"] == "tool")["tool_call_id"] == "call_1"
