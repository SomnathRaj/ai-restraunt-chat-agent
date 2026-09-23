"""AnthropicProviderClient translation tests: neutral shapes <-> the Messages API.

The anthropic SDK client is replaced by a recording fake -- no network.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.ai.agent import ChatAgent
from app.ai.conversation import AssistantMessage, ToolCall, ToolResult, ToolResultsMessage, UserMessage
from app.ai.providers.anthropic_provider import MAX_TOKENS, REFUSAL_FALLBACK_TEXT, AnthropicProviderClient
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable
from app.ai.tool_schemas import TOOL_SCHEMAS
from app.models.db import get_db
from app.services import cart_service

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, status, error_type=None):
    body = {"type": "error", "error": {"type": error_type, "message": "upstream said no"}} if error_type else None
    return cls("upstream said no", response=httpx2.Response(status, request=_REQUEST), body=body)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _thinking(signature):
    return SimpleNamespace(type="thinking", thinking="", signature=signature)


def _tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def _response(*blocks, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=stop_details)


class _RecordingSdk:
    """Returns scripted responses in order (or raises exc); records every request."""

    def __init__(self, responses=None, exc=None):
        self._responses = list(responses or [])
        self._exc = exc
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)


def _generate(sdk, history, tools=None, system_instruction="be helpful"):
    return AnthropicProviderClient(client=sdk, model="claude-test").generate(
        history, tools=tools or [], system_instruction=system_instruction
    )


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------


def test_request_shape_system_max_tokens_and_caching(app):
    sdk = _RecordingSdk([_response(_text("ok"))])
    _generate(sdk, [UserMessage(text="hi")])
    request = sdk.requests[0]
    assert request["model"] == "claude-test"
    assert request["system"] == "be helpful"  # a parameter, not a message
    assert request["max_tokens"] == MAX_TOKENS
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["messages"] == [{"role": "user", "content": "hi"}]
    # Rejected by some current models, so never sent (model is admin free text).
    for key in ("thinking", "temperature", "tool_choice"):
        assert key not in request


def test_translates_neutral_history_to_messages(app):
    sdk = _RecordingSdk([_response(_text("ok"))])
    history = [
        UserMessage(text="Hello"),
        AssistantMessage(text="Hi!"),
        UserMessage(text="Two cokes, less ice"),
        AssistantMessage(
            text="Adding those.",
            tool_calls=[
                ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 2}, id="toolu_1"),
                ToolCall(name="set_item_instructions", args={"item_id": "coke", "instructions": "less ice"}, id="toolu_2"),
            ],
        ),
        ToolResultsMessage(
            results=[
                ToolResult(call_id="toolu_1", name="add_to_cart", response={"ok": True}),
                ToolResult(call_id="toolu_2", name="set_item_instructions", response={"ok": True}),
            ]
        ),
    ]
    _generate(sdk, history)

    assert sdk.requests[0]["messages"] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
        {"role": "user", "content": "Two cokes, less ice"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Adding those."},
                {"type": "tool_use", "id": "toolu_1", "name": "add_to_cart", "input": {"item_id": "coke", "quantity": 2}},
                {"type": "tool_use", "id": "toolu_2", "name": "set_item_instructions", "input": {"item_id": "coke", "instructions": "less ice"}},
            ],
        },
        # ALL results for the turn in ONE user message, paired by ID.
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"ok": true}'},
                {"type": "tool_result", "tool_use_id": "toolu_2", "content": '{"ok": true}'},
            ],
        },
    ]


def test_error_results_are_flagged_and_datetimes_serialized(app):
    sdk = _RecordingSdk([_response(_text("ok"))])
    when = datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc)
    history = [
        UserMessage(text="status?"),
        AssistantMessage(
            tool_calls=[
                ToolCall(name="get_order_status", args={"order_id": "ORD-1"}, id="t1"),
                ToolCall(name="get_menu_item", args={"item_id": "nope"}, id="t2"),
            ]
        ),
        ToolResultsMessage(
            results=[
                ToolResult(call_id="t1", name="get_order_status", response={"created_at": when}),
                ToolResult(call_id="t2", name="get_menu_item", response={"error": "item_not_found", "message": "No item"}),
            ]
        ),
    ]
    _generate(sdk, history)
    ok_result, error_result = sdk.requests[0]["messages"][-1]["content"]
    assert json.loads(ok_result["content"]) == {"created_at": str(when)}
    assert "is_error" not in ok_result
    assert error_result["is_error"] is True


def test_empty_persisted_reply_is_skipped(app):
    # The API rejects empty text blocks; consecutive user turns are allowed.
    sdk = _RecordingSdk([_response(_text("ok"))])
    _generate(sdk, [UserMessage(text="hi"), AssistantMessage(text=""), UserMessage(text="again")])
    assert sdk.requests[0]["messages"] == [{"role": "user", "content": "hi"}, {"role": "user", "content": "again"}]


def test_no_system_or_tools_keys_when_empty(app):
    sdk = _RecordingSdk([_response(_text("ok"))])
    _generate(sdk, [UserMessage(text="hi")], system_instruction="")
    assert "system" not in sdk.requests[0]
    assert "tools" not in sdk.requests[0]


def test_neutral_tool_schemas_become_input_schema_tools(app):
    sdk = _RecordingSdk([_response(_text("ok"))])
    _generate(sdk, [UserMessage(text="hi")], tools=TOOL_SCHEMAS)
    assert sdk.requests[0]["tools"] == [
        {"name": s["name"], "description": s["description"], "input_schema": s["parameters"]} for s in TOOL_SCHEMAS
    ]


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------


def test_parallel_tool_use_blocks_are_parsed_with_ids(app):
    sdk = _RecordingSdk(
        [
            _response(
                _text("Let me add that."),
                _tool_use("toolu_a", "add_to_cart", {"item_id": "coke", "quantity": 2}),
                _tool_use("toolu_b", "get_cart", {}),
                stop_reason="tool_use",
            )
        ]
    )
    turn = _generate(sdk, [UserMessage(text="two cokes")])
    assert turn.text == "Let me add that."
    assert turn.function_calls == [
        ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 2}, id="toolu_a"),
        ToolCall(name="get_cart", args={}, id="toolu_b"),
    ]


def test_tool_turn_keeps_raw_content_for_replay_and_plain_reply_does_not(app):
    blocks = [_thinking("sig-123"), _tool_use("toolu_a", "get_cart", {})]
    turn = _generate(_RecordingSdk([_response(*blocks, stop_reason="tool_use")]), [UserMessage(text="cart")])
    assert turn.provider_metadata == {"content": blocks}

    plain = _generate(_RecordingSdk([_response(_text("Hello!"))]), [UserMessage(text="hi")])
    assert plain.text == "Hello!"
    assert plain.provider_metadata is None


def test_text_blocks_are_concatenated_and_thinking_ignored(app):
    turn = _generate(_RecordingSdk([_response(_thinking("s"), _text("Hello, "), _text("welcome!"))]), [UserMessage(text="hi")])
    assert turn.text == "Hello, welcome!"


def test_refusal_never_returns_a_blank_reply(app):
    refused = _response(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    turn = _generate(_RecordingSdk([refused]), [UserMessage(text="...")])
    assert turn.text == REFUSAL_FALLBACK_TEXT
    assert turn.function_calls == []


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_missing_key_or_model_raises_not_configured(app):
    with pytest.raises(AIProviderNotConfigured):
        AnthropicProviderClient(model="claude-test").generate([UserMessage(text="hi")], tools=[], system_instruction="")
    with pytest.raises(AIProviderNotConfigured):
        AnthropicProviderClient(api_key="sk-ant-x").generate([UserMessage(text="hi")], tools=[], system_instruction="")


@pytest.mark.parametrize(
    ("exc", "expected", "summary"),
    [
        (_status_error(anthropic.RateLimitError, 429, "rate_limit_error"), AIProviderRateLimited, "rate limit"),
        (_status_error(anthropic.AuthenticationError, 401, "authentication_error"), AIProviderUnavailable, "401 authentication_error"),
        (_status_error(anthropic.PermissionDeniedError, 403, "permission_error"), AIProviderUnavailable, "403 permission_error"),
        (_status_error(anthropic.NotFoundError, 404, "not_found_error"), AIProviderUnavailable, "404 not_found_error"),
        (_status_error(anthropic.BadRequestError, 400, "invalid_request_error"), AIProviderUnavailable, "400 invalid_request_error"),
        (_status_error(anthropic.OverloadedError, 529, "overloaded_error"), AIProviderUnavailable, "529 overloaded_error"),
        (_status_error(anthropic.InternalServerError, 500), AIProviderUnavailable, "API error 500"),
        (anthropic.APIConnectionError(request=_REQUEST), AIProviderUnavailable, "network error"),
        (anthropic.APITimeoutError(request=_REQUEST), AIProviderUnavailable, "network error"),
    ],
)
def test_sdk_errors_map_to_neutral_errors(app, exc, expected, summary):
    with pytest.raises(expected) as info:
        _generate(_RecordingSdk(exc=exc), [UserMessage(text="hi")])
    assert info.value.__cause__ is exc
    assert summary in str(info.value)
    assert "upstream said no" not in str(info.value)  # never the error message/body


def test_real_client_uses_short_timeout_and_one_retry(app):
    client = AnthropicProviderClient(api_key="sk-ant-test", model="claude-test")._create_client()
    assert client.max_retries == 1
    assert client.timeout == 60


# ---------------------------------------------------------------------------
# Through ChatAgent: thinking blocks replayed, results grouped, IDs paired
# ---------------------------------------------------------------------------


def test_chat_agent_tool_loop_replays_thinking_and_groups_results(app):
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
    first_blocks = [
        _thinking("sig-abc"),
        _tool_use("toolu_a", "add_to_cart", {"item_id": "coke", "quantity": 2}),
        _tool_use("toolu_b", "set_item_instructions", {"item_id": "coke", "instructions": "less ice"}),
    ]
    sdk = _RecordingSdk([_response(*first_blocks, stop_reason="tool_use"), _response(_text("Added 2 Cokes with less ice!"))])

    reply = ChatAgent(client=AnthropicProviderClient(client=sdk, model="claude-test")).handle_message("s1", "Two cokes, less ice")

    assert reply == "Added 2 Cokes with less ice!"
    assert cart_service.get_cart("s1")["items"] == [
        {"item_id": "coke", "name": "Coke", "price": 60, "quantity": 2, "instructions": "less ice"}
    ]
    second = sdk.requests[1]["messages"]
    # The assistant turn is replayed verbatim -- thinking block (and its signature) included.
    assert second[-2] == {"role": "assistant", "content": first_blocks}
    # Both results in ONE user message, in call order, paired by ID.
    assert second[-1]["role"] == "user"
    assert [block["tool_use_id"] for block in second[-1]["content"]] == ["toolu_a", "toolu_b"]
    # Persisted history stays plain text -- no raw blocks leak into MongoDB.
    history = get_db().chat_sessions.find_one({"session_id": "s1"})["conversation_context"]["history"]
    assert [turn["text"] for turn in history] == ["Two cokes, less ice", "Added 2 Cokes with less ice!"]


def test_real_sdk_serializes_replayed_thinking_blocks_unchanged(app):
    """The fakes above replay SimpleNamespaces; production replays the SDK's
    own pydantic blocks (with None fields like `caller`). Run the REAL SDK
    against a mock HTTP transport and inspect the exact JSON it would send."""
    from anthropic.types import Message

    from app.ai.providers.anthropic_provider import _to_llm_turn

    first = Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "SIG-abc"},
                {"type": "redacted_thinking", "data": "REDACTED-xyz"},
                {"type": "tool_use", "id": "toolu_1", "name": "get_cart", "input": {}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    turn = _to_llm_turn(first)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_2",
                "type": "message",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    sdk = anthropic.Anthropic(api_key="sk-ant-test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    history = [
        UserMessage(text="cart?"),
        AssistantMessage(text=turn.text, tool_calls=turn.function_calls, provider_metadata=turn.provider_metadata),
        ToolResultsMessage(results=[ToolResult(call_id="toolu_1", name="get_cart", response={"items": []})]),
    ]
    reply = AnthropicProviderClient(client=sdk, model="claude-test").generate(history, tools=[], system_instruction="sys")

    assert reply.text == "ok"
    assert sent[0]["messages"][1]["content"] == [
        {"type": "thinking", "thinking": "", "signature": "SIG-abc"},
        {"type": "redacted_thinking", "data": "REDACTED-xyz"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_cart", "input": {}},
    ]
    assert "null" not in json.dumps(sent[0])
