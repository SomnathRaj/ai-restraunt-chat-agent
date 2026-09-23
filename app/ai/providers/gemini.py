"""Gemini adapter: neutral conversation/tool schemas <-> the google-genai SDK.

Contents are built as plain dicts mirroring the Gemini REST wire format
(role/parts, with "function_call"/"function_response" parts) rather than
constructed via SDK helper classes -- this is deliberately version-resilient
since it matches the documented API schema rather than a specific SDK
release's Python object constructors.

Automatic Function Calling is intentionally NOT used: tools are passed as
plain types.FunctionDeclaration objects (no Python callables), so the SDK
never auto-executes a call. Every tool call is routed back through
app/ai/tool_executor.py -> app/services/*, Flask's own validated business
logic (see ARCHITECTURE.md Section 2).

Verified against the real Gemini API on 2026-09-21 (model gemini-3.5-flash-lite):
a function_call part must have its `thought_signature` echoed back verbatim
when it's re-sent as history within the SAME tool loop, or the next call in
the loop fails with a 400 ("Function call is missing a thought_signature").
This adapter stashes it in ToolCall.provider_metadata and re-attaches it when
translating history, so ChatAgent never has to know it exists. It never needs
to survive across separate /api/chat requests -- persisted session history
stores only final text replies, never raw function_call parts.
"""

import logging

import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.ai.conversation import AssistantMessage, LLMTurn, Message, ToolCall, ToolResultsMessage, UserMessage
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable

log = logging.getLogger(__name__)

THOUGHT_SIGNATURE = "thought_signature"


def _to_contents(history: list[Message]) -> list[dict]:
    contents = []
    for message in history:
        if isinstance(message, UserMessage):
            contents.append({"role": "user", "parts": [{"text": message.text}]})
        elif isinstance(message, AssistantMessage):
            parts = []
            # A plain reply always carries a text part (even if empty) --
            # Gemini rejects a content with no parts at all.
            if message.text or not message.tool_calls:
                parts.append({"text": message.text})
            for call in message.tool_calls:
                part = {"function_call": {"name": call.name, "args": call.args}}
                signature = (call.provider_metadata or {}).get(THOUGHT_SIGNATURE)
                if signature is not None:
                    part[THOUGHT_SIGNATURE] = signature
                parts.append(part)
            contents.append({"role": "model", "parts": parts})
        elif isinstance(message, ToolResultsMessage):
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {"function_response": {"name": result.name, "response": result.response}}
                        for result in message.results
                    ],
                }
            )
    return contents


def _to_function_declarations(tools: list[dict]) -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name=tool["name"], description=tool["description"], parameters_json_schema=tool["parameters"]
        )
        for tool in tools
    ]


def _to_llm_turn(response) -> LLMTurn:
    turn = LLMTurn()
    candidate = response.candidates[0] if response.candidates else None
    if candidate is None or candidate.content is None:
        return turn

    for part in candidate.content.parts or []:
        if getattr(part, "function_call", None):
            signature = getattr(part, THOUGHT_SIGNATURE, None)
            turn.function_calls.append(
                ToolCall(
                    name=part.function_call.name,
                    args=dict(part.function_call.args or {}),
                    provider_metadata={THOUGHT_SIGNATURE: signature} if signature is not None else None,
                )
            )
        elif getattr(part, "text", None):
            turn.text += part.text

    return turn


class GeminiProviderClient:
    """Owns the actual SDK call. app/ai/agent.py depends on the neutral interface, not the SDK.

    The API key and model come from the admin-managed ai_providers record
    via app/ai/providers/registry.py -- never from the environment. `client`
    injects a stand-in SDK client for tests.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None):
        self._api_key = api_key
        self._model = model
        self._client = client

    def generate(self, history: list[Message], tools: list[dict], system_instruction: str) -> LLMTurn:
        if self._client is None and not self._api_key:
            raise AIProviderNotConfigured("no Gemini API key")
        if not self._model:
            raise AIProviderNotConfigured("no Gemini model")
        client = self._client or genai.Client(api_key=self._api_key)

        try:
            response = client.models.generate_content(
                model=self._model,
                contents=_to_contents(history),
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[types.Tool(function_declarations=_to_function_declarations(tools))] if tools else None,
                ),
            )
        except APIError as err:
            # Covers real failures hit during development: quota exhaustion
            # (429), upstream 5xx, and a deprecated/unknown model name (404).
            # Log the status server-side only -- the raw SDK exception can
            # include response bodies and must never reach the customer.
            log.warning("gemini_api_error", extra={"status_code": err.code})
            if err.code == 429:
                raise AIProviderRateLimited("Gemini rate limit exceeded") from err
            raise AIProviderUnavailable(f"Gemini API error {err.code}") from err
        except httpx.HTTPError as err:
            # Network-level failure below the API-response layer (connection
            # refused, DNS failure, or a genuine timeout -- PRD Section 76).
            log.warning("gemini_network_error", extra={"error_type": type(err).__name__})
            raise AIProviderUnavailable("Gemini network error") from err

        return _to_llm_turn(response)
