"""OpenAIProviderClient translation tests: neutral shapes <-> Chat Completions.

The openai SDK client is replaced by a recording fake -- no network.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx2
import openai
import pytest

from app.ai.agent import ChatAgent
from app.ai.conversation import AssistantMessage, ToolCall, ToolResult, ToolResultsMessage, UserMessage
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable
from app.ai.providers.openai_provider import OpenAIProviderClient
from app.ai.tool_schemas import TOOL_SCHEMAS
from app.models.db import get_db
from app.services import cart_service

_REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status_error(cls, status, code=None):
    return cls("upstream said no", response=httpx2.Response(status, request=_REQUEST), body={"code": code} if code else None)


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments))


def _response(content=None, tool_calls=None, refusal=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _RecordingSdk:
    """Returns scripted responses in order (or raises exc); records every request."""

    def __init__(self, responses=None, exc=None):
        self._responses = list(responses or [])
        self._exc = exc
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Snapshot -- the caller builds a fresh list each call, but be safe.
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)


def _generate(sdk, history, tools=None, system_instruction="be helpful"):
    return OpenAIProviderClient(client=sdk, model="gpt-test").generate(
        history, tools=tools or [], system_instruction=system_instruction
    )


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------


def test_translates_neutral_history_to_chat_messages(app):
    sdk = _RecordingSdk([_response(content="ok")])
    history = [
        UserMessage(text="Hello"),
        AssistantMessage(text="Hi!"),
        UserMessage(text="Two cokes, less ice"),
        AssistantMessage(
            tool_calls=[
                ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 2}, id="call_1"),
                ToolCall(name="set_item_instructions", args={"item_id": "coke", "instructions": "less ice"}, id="call_2"),
            ]
        ),
        ToolResultsMessage(
            results=[
                ToolResult(call_id="call_1", name="add_to_cart", response={"ok": True}),
                ToolResult(call_id="call_2", name="set_item_instructions", response={"ok": True}),
            ]
        ),
    ]
    _generate(sdk, history)

    assert sdk.requests[0]["messages"] == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "Two cokes, less ice"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "add_to_cart", "arguments": '{"item_id": "coke", "quantity": 2}'}},
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "set_item_instructions", "arguments": '{"item_id": "coke", "instructions": "less ice"}'},
                },
            ],
        },
        # One "tool" message per result, in call order, paired by ID.
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "call_2", "content": '{"ok": true}'},
    ]
    assert sdk.requests[0]["model"] == "gpt-test"


def test_assistant_text_alongside_tool_calls_is_kept(app):
    sdk = _RecordingSdk([_response(content="ok")])
    history = [UserMessage(text="hi"), AssistantMessage(text="Let me check.", tool_calls=[ToolCall(name="get_cart", args={}, id="c1")])]
    _generate(sdk, history)
    assert sdk.requests[0]["messages"][2]["content"] == "Let me check."


def test_tool_results_with_datetimes_are_serialized(app):
    sdk = _RecordingSdk([_response(content="ok")])
    when = datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc)
    history = [
        UserMessage(text="status?"),
        AssistantMessage(tool_calls=[ToolCall(name="get_order_status", args={"order_id": "ORD-1"}, id="c1")]),
        ToolResultsMessage(results=[ToolResult(call_id="c1", name="get_order_status", response={"created_at": when})]),
    ]
    _generate(sdk, history)
    assert json.loads(sdk.requests[0]["messages"][-1]["content"]) == {"created_at": str(when)}


def test_no_system_message_when_instruction_is_empty(app):
    sdk = _RecordingSdk([_response(content="ok")])
    _generate(sdk, [UserMessage(text="hi")], system_instruction="")
    assert sdk.requests[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_neutral_tool_schemas_become_function_tools(app):
    sdk = _RecordingSdk([_response(content="ok")])
    _generate(sdk, [UserMessage(text="hi")], tools=TOOL_SCHEMAS)

    tools = sdk.requests[0]["tools"]
    assert len(tools) == len(TOOL_SCHEMAS)
    for tool, schema in zip(tools, TOOL_SCHEMAS):
        assert tool == {
            "type": "function",
            "function": {"name": schema["name"], "description": schema["description"], "parameters": schema["parameters"]},
        }


def test_no_tools_key_when_there_are_no_tools(app):
    sdk = _RecordingSdk([_response(content="ok")])
    _generate(sdk, [UserMessage(text="hi")])
    assert "tools" not in sdk.requests[0]


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------


def test_parallel_tool_calls_are_parsed_with_ids(app):
    sdk = _RecordingSdk(
        [
            _response(
                tool_calls=[
                    _tool_call("call_a", "add_to_cart", '{"item_id": "coke", "quantity": 2}'),
                    _tool_call("call_b", "get_cart", "{}"),
                ]
            )
        ]
    )
    turn = _generate(sdk, [UserMessage(text="two cokes")])
    assert turn.text == ""
    assert turn.function_calls == [
        ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 2}, id="call_a"),
        ToolCall(name="get_cart", args={}, id="call_b"),
    ]
    assert all(call.provider_metadata is None for call in turn.function_calls)


@pytest.mark.parametrize("arguments", ["{not json", '["a list"]', None, ""])
def test_malformed_arguments_become_an_empty_dict(app, arguments):
    sdk = _RecordingSdk([_response(tool_calls=[_tool_call("c1", "get_cart", arguments)])])
    turn = _generate(sdk, [UserMessage(text="cart")])
    assert turn.function_calls[0].args == {}


def test_text_reply_and_refusal(app):
    assert _generate(_RecordingSdk([_response(content="Hello!")]), [UserMessage(text="hi")]).text == "Hello!"
    refused = _generate(_RecordingSdk([_response(refusal="I can't help with that.")]), [UserMessage(text="hi")])
    assert refused.text == "I can't help with that."


def test_response_with_no_choices_returns_empty_turn(app):
    turn = _generate(_RecordingSdk([SimpleNamespace(choices=[])]), [UserMessage(text="hi")])
    assert turn.text == "" and turn.function_calls == []


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_missing_key_or_model_raises_not_configured(app):
    with pytest.raises(AIProviderNotConfigured):
        OpenAIProviderClient(model="gpt-test").generate([UserMessage(text="hi")], tools=[], system_instruction="")
    with pytest.raises(AIProviderNotConfigured):
        OpenAIProviderClient(api_key="sk-x").generate([UserMessage(text="hi")], tools=[], system_instruction="")


@pytest.mark.parametrize(
    ("exc", "expected", "summary"),
    [
        (_status_error(openai.RateLimitError, 429, "rate_limit_exceeded"), AIProviderRateLimited, "rate limit"),
        # Out of credit is not a burst limit -- retrying won't help. Older shape:
        (_status_error(openai.RateLimitError, 429, "insufficient_quota"), AIProviderUnavailable, "429 insufficient_quota"),
        # Current shape, seen live 2026-09-23: the type says it, the code doesn't.
        (
            openai.RateLimitError(
                "no credits",
                response=httpx2.Response(429, request=_REQUEST),
                body={"type": "insufficient_quota", "code": "credit_balance_exhausted"},
            ),
            AIProviderUnavailable,
            "429 insufficient_quota",
        ),
        (_status_error(openai.AuthenticationError, 401, "invalid_api_key"), AIProviderUnavailable, "401 invalid_api_key"),
        (_status_error(openai.NotFoundError, 404, "model_not_found"), AIProviderUnavailable, "404 model_not_found"),
        (_status_error(openai.BadRequestError, 400), AIProviderUnavailable, "API error 400"),
        (_status_error(openai.InternalServerError, 500), AIProviderUnavailable, "API error 500"),
        (openai.APIConnectionError(request=_REQUEST), AIProviderUnavailable, "network error"),
        (openai.APITimeoutError(request=_REQUEST), AIProviderUnavailable, "network error"),
    ],
)
def test_sdk_errors_map_to_neutral_errors(app, exc, expected, summary):
    with pytest.raises(expected) as info:
        _generate(_RecordingSdk(exc=exc), [UserMessage(text="hi")])
    assert info.value.__cause__ is exc
    assert summary in str(info.value)
    assert "upstream said no" not in str(info.value)  # never the response body


def test_real_client_uses_short_timeout_and_one_retry(app):
    client = OpenAIProviderClient(api_key="sk-test", model="gpt-test")._create_client()
    assert client.max_retries == 1
    assert client.timeout == 60


# ---------------------------------------------------------------------------
# Through ChatAgent: IDs survive a real multi-call tool loop
# ---------------------------------------------------------------------------


def test_chat_agent_tool_loop_pairs_results_with_call_ids(app):
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
    sdk = _RecordingSdk(
        [
            _response(
                tool_calls=[
                    _tool_call("call_a", "add_to_cart", '{"item_id": "coke", "quantity": 2}'),
                    _tool_call("call_b", "set_item_instructions", '{"item_id": "coke", "instructions": "less ice"}'),
                ]
            ),
            _response(content="Added 2 Cokes with less ice!"),
        ]
    )
    reply = ChatAgent(client=OpenAIProviderClient(client=sdk, model="gpt-test")).handle_message("s1", "Two cokes, less ice")

    assert reply == "Added 2 Cokes with less ice!"
    assert cart_service.get_cart("s1")["items"] == [
        {"item_id": "coke", "name": "Coke", "price": 60, "quantity": 2, "instructions": "less ice"}
    ]
    second = sdk.requests[1]["messages"]
    tool_messages = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_a", "call_b"]
    assert [c["id"] for c in second[-3]["tool_calls"]] == ["call_a", "call_b"]


def test_chat_agent_recovers_from_malformed_arguments(app):
    # Unparseable arguments -> {} -> executor's invalid_arguments result ->
    # the model gets a structured error and can retry, no 500.
    sdk = _RecordingSdk(
        [
            _response(tool_calls=[_tool_call("c1", "search_menu", "{oops")]),
            _response(content="Sorry, what would you like to search for?"),
        ]
    )
    reply = ChatAgent(client=OpenAIProviderClient(client=sdk, model="gpt-test")).handle_message("s1", "find")
    assert reply == "Sorry, what would you like to search for?"
    tool_result = json.loads(sdk.requests[1]["messages"][-1]["content"])
    assert tool_result["error"] == "invalid_arguments"
    assert "query" in tool_result["message"]
