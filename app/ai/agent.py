"""ChatAgent: owns the multi-turn Gemini tool-calling loop (ARCHITECTURE.md Section 2).

Contents are built as plain dicts mirroring the Gemini REST wire format
(role/parts, with "function_call"/"function_response" parts) rather than
constructed via SDK helper classes -- this is deliberately version-resilient
since it matches the documented API schema rather than a specific SDK
release's Python object constructors.

Verified against the real Gemini API on 2026-09-21 (model gemini-3.5-flash-lite):
a function_call part must have its `thought_signature` echoed back verbatim
when it's re-sent as history within the SAME tool loop, or the next call in
the loop fails with a 400 ("Function call is missing a thought_signature").
See ToolCall.thought_signature in gemini_client.py. This only matters within
a single handle_message() call's own loop -- conversation history persisted
to MongoDB (session_service.append_turn) stores only the final text reply,
never raw function_call parts, so no signature needs to survive across
separate /api/chat requests.
"""

import logging

from flask import current_app

from app.ai.gemini_client import GeminiClient
from app.ai.system_prompt import build_system_prompt
from app.ai.tool_executor import ToolExecutor, UnknownToolError
from app.ai.tool_schemas import TOOL_DECLARATIONS
from app.services import session_service

log = logging.getLogger(__name__)

FRIENDLY_FALLBACK_MESSAGE = "I'm having a little trouble with that right now. Please try again in a moment."


def _history_to_contents(history: list[dict]) -> list[dict]:
    return [{"role": turn["role"], "parts": [{"text": turn["text"]}]} for turn in history]


def _json_safe(result) -> dict:
    if isinstance(result, dict):
        return result
    return {"result": result}


class ChatAgent:
    def __init__(self, client: GeminiClient | None = None, executor: ToolExecutor | None = None, max_iterations: int | None = None):
        self.client = client or GeminiClient()
        self.executor = executor or ToolExecutor()
        self.max_iterations = max_iterations if max_iterations is not None else current_app.config["TOOL_LOOP_MAX_ITERATIONS"]

    def handle_message(self, session_id: str, message: str) -> str:
        session = session_service.get_or_create(session_id)
        history = session.get("conversation_context", {}).get("history", [])
        contents = _history_to_contents(history) + [{"role": "user", "parts": [{"text": message}]}]
        system_instruction = build_system_prompt()

        for _ in range(self.max_iterations):
            turn = self.client.generate(contents, tools=TOOL_DECLARATIONS, system_instruction=system_instruction)

            if not turn.function_calls:
                session_service.append_turn(session_id, message, turn.text)
                return turn.text

            model_parts = []
            if turn.text:
                model_parts.append({"text": turn.text})
            for call in turn.function_calls:
                part = {"function_call": {"name": call.name, "args": call.args}}
                if call.thought_signature is not None:
                    part["thought_signature"] = call.thought_signature
                model_parts.append(part)
            contents.append({"role": "model", "parts": model_parts})

            # Execute sequentially -- calls in one turn may mutate the same cart doc.
            response_parts = []
            for call in turn.function_calls:
                try:
                    result = self.executor.execute(call.name, call.args, session_id)
                except UnknownToolError:
                    # Gemini should never request a name outside TOOL_DECLARATIONS --
                    # if it somehow does, give it a structured signal to recover
                    # from rather than crashing the whole customer-facing request.
                    result = {"error": "unknown_tool", "tool": call.name}
                response_parts.append({"function_response": {"name": call.name, "response": _json_safe(result)}})
            contents.append({"role": "user", "parts": response_parts})

        log.warning("tool_loop_exceeded", extra={"session_id": session_id})
        session_service.append_turn(session_id, message, FRIENDLY_FALLBACK_MESSAGE)
        return FRIENDLY_FALLBACK_MESSAGE
