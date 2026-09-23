"""ChatAgent: owns the multi-turn tool-calling loop (ARCHITECTURE.md Section 2).

The loop builds and passes ONLY the provider-neutral conversation types from
app/ai/conversation.py. Translating them to a provider's wire format -- and
any provider-specific quirk such as Gemini's thought_signature -- lives in
that provider's adapter under app/ai/providers/ (MULTI_AI_PROVIDER_DESIGN.md
Section 3). ToolCall/LLMTurn provider_metadata is carried back untouched, never read.

Conversation history persisted to MongoDB (session_service.append_turn)
stores only final text pairs, never raw tool-call turns, so each
handle_message() call rebuilds a clean neutral conversation from text alone.
"""

import logging

from flask import current_app

from app.ai.conversation import AssistantMessage, Message, ToolResult, ToolResultsMessage, UserMessage
from app.ai.providers.base import AIProviderClient
from app.ai.providers.registry import get_active_ai_client
from app.ai.system_prompt import build_system_prompt
from app.ai.tool_executor import ToolExecutor, UnknownToolError
from app.ai.tool_schemas import TOOL_SCHEMAS
from app.services import session_service

log = logging.getLogger(__name__)

FRIENDLY_FALLBACK_MESSAGE = "I'm having a little trouble with that right now. Please try again in a moment."


def _history_to_messages(history: list[dict]) -> list[Message]:
    # Persisted turns use role "model" for the assistant (Gemini's naming,
    # kept as-is so existing sessions need no data migration).
    return [
        UserMessage(text=turn["text"]) if turn["role"] == "user" else AssistantMessage(text=turn["text"])
        for turn in history
    ]


def _json_safe(result) -> dict:
    if isinstance(result, dict):
        return result
    return {"result": result}


class ChatAgent:
    def __init__(self, client: AIProviderClient | None = None, executor: ToolExecutor | None = None, max_iterations: int | None = None):
        # None -> resolve the admin-selected provider fresh on each message
        # (MULTI_AI_PROVIDER_DESIGN.md Section 6). Tests inject a fake here.
        self.client = client
        self.executor = executor or ToolExecutor()
        self.max_iterations = max_iterations if max_iterations is not None else current_app.config["TOOL_LOOP_MAX_ITERATIONS"]

    def handle_message(self, session_id: str, message: str) -> str:
        client = self.client or get_active_ai_client()
        session = session_service.get_or_create(session_id)
        history = session.get("conversation_context", {}).get("history", [])
        conversation = _history_to_messages(history) + [UserMessage(text=message)]
        system_instruction = build_system_prompt()

        for _ in range(self.max_iterations):
            turn = client.generate(conversation, tools=TOOL_SCHEMAS, system_instruction=system_instruction)

            if not turn.function_calls:
                session_service.append_turn(session_id, message, turn.text)
                return turn.text

            conversation.append(
                AssistantMessage(
                    text=turn.text, tool_calls=list(turn.function_calls), provider_metadata=turn.provider_metadata
                )
            )

            # Execute sequentially -- calls in one turn may mutate the same cart doc.
            results = []
            for call in turn.function_calls:
                try:
                    result = self.executor.execute(call.name, call.args, session_id)
                except UnknownToolError:
                    # The model should never request a name outside TOOL_SCHEMAS --
                    # if it somehow does, give it a structured signal to recover
                    # from rather than crashing the whole customer-facing request.
                    result = {"error": "unknown_tool", "tool": call.name}
                results.append(ToolResult(call_id=call.id, name=call.name, response=_json_safe(result)))
            conversation.append(ToolResultsMessage(results=results))

        log.warning("tool_loop_exceeded", extra={"session_id": session_id})
        session_service.append_turn(session_id, message, FRIENDLY_FALLBACK_MESSAGE)
        return FRIENDLY_FALLBACK_MESSAGE
