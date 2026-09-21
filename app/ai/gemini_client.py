"""Thin wrapper around the google-genai SDK.

`generate()` returns an internal LLMTurn dataclass rather than the raw SDK
response -- this decouples app/ai/agent.py from the SDK's response shape,
which is what makes the tool-execution loop unit-testable with a scripted
FakeGeminiClient and zero network access (see ARCHITECTURE.md Section 9).

Automatic Function Calling is intentionally NOT used: tool declarations are
passed as plain types.FunctionDeclaration objects (no Python callables), so
the SDK never auto-executes a call. Every tool call is routed back through
app/ai/tool_executor.py -> app/services/*, Flask's own validated business
logic (see ARCHITECTURE.md Section 2).
"""

import logging
from dataclasses import dataclass, field

from flask import current_app
from google import genai
from google.genai import types

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    args: dict
    # Opaque continuation token the API requires to be echoed back verbatim
    # on the corresponding function_call part when this turn is re-sent as
    # history within the SAME tool-calling loop (see agent.py) -- omitting
    # it makes the next call in the loop fail with
    # "Function call is missing a thought_signature in functionCall parts."
    # Verified against the installed google-genai SDK/API on 2026-09-21.
    thought_signature: bytes | None = None


@dataclass
class LLMTurn:
    text: str = ""
    function_calls: list[ToolCall] = field(default_factory=list)


class GeminiNotConfigured(Exception):
    """Raised when a Gemini-backed operation runs before GEMINI_API_KEY is set."""


def _create_client():
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def get_client():
    """Return a genai.Client cached on the app instance, or None if unconfigured.

    Cached on current_app.extensions (not flask.g), mirroring app/models/db.py --
    meant to be a long-lived singleton per process, not recreated per request.
    """
    if "gemini_client" not in current_app.extensions:
        current_app.extensions["gemini_client"] = _create_client()
    return current_app.extensions["gemini_client"]


def is_configured() -> bool:
    return bool(current_app.config.get("GEMINI_API_KEY"))


class GeminiClient:
    """Owns the actual SDK call. app/ai/agent.py depends on this interface, not the SDK."""

    def __init__(self, client=None, model: str | None = None):
        self._client = client
        self._model = model

    def generate(self, contents: list, tools: list[types.FunctionDeclaration], system_instruction: str) -> LLMTurn:
        client = self._client or get_client()
        if client is None:
            raise GeminiNotConfigured("GEMINI_API_KEY is not set")
        model = self._model or current_app.config["GEMINI_MODEL"]

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=[types.Tool(function_declarations=tools)] if tools else None,
            ),
        )

        turn = LLMTurn()
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.content is None:
            return turn

        for part in candidate.content.parts or []:
            if getattr(part, "function_call", None):
                turn.function_calls.append(
                    ToolCall(
                        name=part.function_call.name,
                        args=dict(part.function_call.args or {}),
                        thought_signature=getattr(part, "thought_signature", None),
                    )
                )
            elif getattr(part, "text", None):
                turn.text += part.text

        return turn
