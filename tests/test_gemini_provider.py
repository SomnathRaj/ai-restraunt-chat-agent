"""GeminiProviderClient translation tests: neutral shapes <-> Gemini wire format.

The google-genai SDK client is replaced by a recording fake -- no network.
Error mapping end-to-end through /api/chat is covered in tests/test_chat_api.py.
"""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import types
from google.genai.errors import APIError

from app.ai.conversation import AssistantMessage, ToolCall, ToolResult, ToolResultsMessage, UserMessage
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable
from app.ai.providers.gemini import GeminiProviderClient
from app.ai.tool_schemas import TOOL_SCHEMAS


class _RecordingSdk:
    def __init__(self, parts=None, exc=None):
        self._parts = parts
        self._exc = exc
        self.requests = []
        self.models = SimpleNamespace(generate_content=self._generate_content)

    def _generate_content(self, **kwargs):
        self.requests.append(kwargs)
        if self._exc is not None:
            raise self._exc
        if self._parts is None:
            return SimpleNamespace(candidates=[])
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=self._parts))])


def _text_part(text):
    return SimpleNamespace(text=text, function_call=None, thought_signature=None)


def _call_part(name, args, signature=None):
    return SimpleNamespace(text=None, function_call=SimpleNamespace(name=name, args=args), thought_signature=signature)


def _generate(sdk, history, tools=None):
    return GeminiProviderClient(client=sdk, model="test-model").generate(
        history, tools=tools or [], system_instruction="be helpful"
    )


def test_translates_neutral_history_to_gemini_contents(app):
    sdk = _RecordingSdk(parts=[_text_part("ok")])
    history = [
        UserMessage(text="Hello"),
        AssistantMessage(text="Hi!"),
        UserMessage(text="Two cokes"),
        AssistantMessage(
            tool_calls=[ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 2})]
        ),
        ToolResultsMessage(results=[ToolResult(call_id=None, name="add_to_cart", response={"ok": True})]),
    ]
    _generate(sdk, history)

    assert sdk.requests[0]["contents"] == [
        {"role": "user", "parts": [{"text": "Hello"}]},
        {"role": "model", "parts": [{"text": "Hi!"}]},
        {"role": "user", "parts": [{"text": "Two cokes"}]},
        {"role": "model", "parts": [{"function_call": {"name": "add_to_cart", "args": {"item_id": "coke", "quantity": 2}}}]},
        {"role": "user", "parts": [{"function_response": {"name": "add_to_cart", "response": {"ok": True}}}]},
    ]


def test_assistant_turn_with_text_and_tool_calls_keeps_both_parts(app):
    sdk = _RecordingSdk(parts=[_text_part("ok")])
    history = [
        UserMessage(text="hi"),
        AssistantMessage(text="Let me check.", tool_calls=[ToolCall(name="get_cart", args={})]),
    ]
    _generate(sdk, history)

    assert sdk.requests[0]["contents"][1]["parts"] == [
        {"text": "Let me check."},
        {"function_call": {"name": "get_cart", "args": {}}},
    ]


def test_empty_plain_assistant_reply_still_sends_a_text_part(app):
    # Gemini rejects a content with no parts -- a persisted empty reply must
    # still translate to [{"text": ""}], exactly as before the refactor.
    sdk = _RecordingSdk(parts=[_text_part("ok")])
    _generate(sdk, [UserMessage(text="hi"), AssistantMessage(text=""), UserMessage(text="again")])

    assert sdk.requests[0]["contents"][1] == {"role": "model", "parts": [{"text": ""}]}


def test_thought_signature_is_echoed_back_from_provider_metadata(app):
    sdk = _RecordingSdk(parts=[_text_part("ok")])
    call = ToolCall(name="get_cart", args={}, provider_metadata={"thought_signature": b"opaque-token"})
    _generate(sdk, [UserMessage(text="cart?"), AssistantMessage(tool_calls=[call])])

    function_call_part = sdk.requests[0]["contents"][1]["parts"][0]
    assert function_call_part["thought_signature"] == b"opaque-token"


def test_thought_signature_is_omitted_when_absent(app):
    sdk = _RecordingSdk(parts=[_text_part("ok")])
    _generate(sdk, [UserMessage(text="cart?"), AssistantMessage(tool_calls=[ToolCall(name="get_cart", args={})])])

    function_call_part = sdk.requests[0]["contents"][1]["parts"][0]
    assert "thought_signature" not in function_call_part


def test_response_function_calls_capture_thought_signature_in_provider_metadata(app):
    sdk = _RecordingSdk(
        parts=[
            _call_part("add_to_cart", {"item_id": "coke", "quantity": 1}, signature=b"sig-1"),
            _call_part("get_cart", None),
        ]
    )
    turn = _generate(sdk, [UserMessage(text="one coke")])

    assert turn.text == ""
    assert turn.function_calls == [
        ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 1}, provider_metadata={"thought_signature": b"sig-1"}),
        ToolCall(name="get_cart", args={}, provider_metadata=None),
    ]


def test_response_text_parts_are_concatenated(app):
    sdk = _RecordingSdk(parts=[_text_part("Hello, "), _text_part("welcome!")])
    turn = _generate(sdk, [UserMessage(text="hi")])
    assert turn.text == "Hello, welcome!"
    assert turn.function_calls == []


def test_response_with_no_candidates_returns_empty_turn(app):
    turn = _generate(_RecordingSdk(parts=None), [UserMessage(text="hi")])
    assert turn.text == ""
    assert turn.function_calls == []


def test_neutral_tool_schemas_become_function_declarations(app):
    sdk = _RecordingSdk(parts=[_text_part("ok")])
    _generate(sdk, [UserMessage(text="hi")], tools=TOOL_SCHEMAS)

    config = sdk.requests[0]["config"]
    declarations = config.tools[0].function_declarations
    assert all(isinstance(decl, types.FunctionDeclaration) for decl in declarations)
    assert [decl.name for decl in declarations] == [schema["name"] for schema in TOOL_SCHEMAS]
    assert [decl.parameters_json_schema for decl in declarations] == [schema["parameters"] for schema in TOOL_SCHEMAS]
    assert config.system_instruction == "be helpful"
    assert sdk.requests[0]["model"] == "test-model"


def test_no_tools_sends_no_tool_config(app):
    sdk = _RecordingSdk(parts=[_text_part("ok")])
    _generate(sdk, [UserMessage(text="hi")], tools=[])
    assert sdk.requests[0]["config"].tools is None


def test_every_tool_schema_is_neutral_and_complete():
    for schema in TOOL_SCHEMAS:
        assert set(schema) == {"name", "description", "parameters"}, schema.get("name")
        assert schema["parameters"]["type"] == "object"


def test_missing_api_key_raises_not_configured(app):
    # No key passed to the adapter.
    with pytest.raises(AIProviderNotConfigured):
        GeminiProviderClient().generate([UserMessage(text="hi")], tools=[], system_instruction="")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (APIError(429, {"error": {"message": "quota"}}), AIProviderRateLimited),
        (APIError(500, {"error": {"message": "internal"}}), AIProviderUnavailable),
        (APIError(503, {"error": {"message": "overloaded"}}), AIProviderUnavailable),
        (APIError(404, {"error": {"message": "model not found"}}), AIProviderUnavailable),
        (httpx.ConnectError("refused"), AIProviderUnavailable),
        (httpx.ReadTimeout("timed out"), AIProviderUnavailable),
    ],
)
def test_sdk_errors_map_to_neutral_errors(app, exc, expected):
    with pytest.raises(expected) as info:
        _generate(_RecordingSdk(exc=exc), [UserMessage(text="hi")])
    assert info.value.__cause__ is exc
