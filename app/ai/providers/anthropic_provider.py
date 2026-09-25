"""Anthropic adapter: neutral conversation/tool schemas <-> the Claude Messages API.

Differences from the other adapters that this adapter owns:
- The system prompt is the `system` parameter, not a message.
- Tools are {"name", "description", "input_schema"} (plain JSON Schema).
- A tool_use block has an ID; ALL results for one turn go back as
  tool_result blocks in ONE user message (splitting them across messages
  teaches Claude to stop making parallel calls).
- When the model thinks before calling tools, its thinking blocks must be
  echoed back unchanged on the next call in the same loop. The raw
  response content is stashed in LLMTurn.provider_metadata and replayed
  verbatim -- ChatAgent never looks at it. Like Gemini's thought_signature,
  it never survives past one handle_message(): persisted history is text only.
- The API rejects empty text blocks, so an empty persisted reply is skipped
  (consecutive user messages are allowed; the API merges them).

Deliberately NOT set, because the model is free text chosen by an admin
and these are rejected by some current models: `thinking` (each model's
default applies), `temperature`, and a forced `tool_choice`.

As with every adapter, tools are schema-only; the model can only request a
call, and every call goes through app/ai/tool_executor.py.
"""

import json
import logging

import anthropic

from app.ai.conversation import AssistantMessage, LLMTurn, Message, ToolCall, ToolResultsMessage, UserMessage
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable

log = logging.getLogger(__name__)

# The SDK defaults (10-minute timeout, 2 retries) are far too patient for a
# customer waiting on a chat reply.
REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 1
# Replies are short, but thinking counts against this cap -- too low
# truncates mid-thought. Well under the non-streaming SDK limit.
MAX_TOKENS = 16000

# Shown only if Claude declines (stop_reason "refusal") without any text,
# so the customer never gets a blank reply.
REFUSAL_FALLBACK_TEXT = "Sorry, I can't help with that. Is there something from our menu I can help you with?"

_RAW_CONTENT = "content"


def _to_messages(history: list[Message]) -> list[dict]:
    messages = []
    for message in history:
        if isinstance(message, UserMessage):
            messages.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            raw = (message.provider_metadata or {}).get(_RAW_CONTENT)
            if raw is not None:
                # Same loop, same model: replay exactly what Claude sent,
                # thinking blocks and signatures included.
                messages.append({"role": "assistant", "content": raw})
                continue
            content = [{"type": "text", "text": message.text}] if message.text else []
            content += [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
                for call in message.tool_calls
            ]
            if content:
                messages.append({"role": "assistant", "content": content})
        elif isinstance(message, ToolResultsMessage):
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": result.call_id,
                            # default=str: service results can include datetimes.
                            "content": json.dumps(result.response, default=str),
                            **({"is_error": True} if "error" in result.response else {}),
                        }
                        for result in message.results
                    ],
                }
            )
    return messages


def _to_tools(tools: list[dict]) -> list[dict]:
    return [
        {"name": tool["name"], "description": tool["description"], "input_schema": tool["parameters"]}
        for tool in tools
    ]


def _to_llm_turn(response) -> LLMTurn:
    blocks = response.content or []
    text = "".join(block.text for block in blocks if block.type == "text")
    calls = [
        ToolCall(name=block.name, args=dict(block.input or {}), id=block.id)
        for block in blocks
        if block.type == "tool_use"
    ]

    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None) if response.stop_details else None
        log.warning("anthropic_refusal", extra={"category": category})
        return LLMTurn(text=text or REFUSAL_FALLBACK_TEXT)

    return LLMTurn(
        text=text,
        function_calls=calls,
        provider_metadata={_RAW_CONTENT: blocks} if calls else None,
    )


def _error_type(err: anthropic.APIStatusError) -> str | None:
    """The short machine-readable type (e.g. "not_found_error") -- never the message."""
    body = err.body if isinstance(err.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    return error.get("type")


class AnthropicProviderClient:
    """Owns the actual SDK call. app/ai/agent.py depends on the neutral interface, not the SDK.

    The API key and model come from the admin-managed ai_providers record
    via app/ai/providers/registry.py. `client` injects a stand-in SDK client
    for tests.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None):
        self._api_key = api_key
        self._model = model
        self._client = client

    def _create_client(self):
        return anthropic.Anthropic(api_key=self._api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES)

    def generate(self, history: list[Message], tools: list[dict], system_instruction: str) -> LLMTurn:
        if self._client is None and not self._api_key:
            raise AIProviderNotConfigured("no Anthropic API key")
        if not self._model:
            raise AIProviderNotConfigured("no Anthropic model")
        # Built once per adapter, then reused, so every round-trip of one
        # message's tool loop shares a single HTTPS connection. The
        # registry builds a fresh adapter per customer message, so
        # nothing (including the key) outlives that message.
        if self._client is None:
            self._client = self._create_client()
        client = self._client

        request = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            "messages": _to_messages(history),
            # Caches the stable prefix (tools + system prompt + earlier turns)
            # so each round-trip of the tool loop re-reads it at ~10% of the
            # input price. Silently a no-op if the prefix is below the
            # model's minimum cacheable length.
            "cache_control": {"type": "ephemeral"},
        }
        if system_instruction:
            request["system"] = system_instruction
        if tools:
            request["tools"] = _to_tools(tools)

        try:
            response = client.messages.create(**request)
        except anthropic.RateLimitError as err:
            log.warning("anthropic_api_error", extra={"status_code": 429, "error_type": _error_type(err)})
            raise AIProviderRateLimited("Anthropic rate limit exceeded") from err
        except anthropic.APIStatusError as err:
            # 401 bad key, 403, 404 unknown model, 400, 529 overloaded, 5xx.
            # Only the status and short error type are kept -- never the body.
            error_type = _error_type(err)
            log.warning("anthropic_api_error", extra={"status_code": err.status_code, "error_type": error_type})
            summary = f"Anthropic API error {err.status_code}" + (f" {error_type}" if error_type else "")
            raise AIProviderUnavailable(summary) from err
        except anthropic.APIConnectionError as err:
            # Includes APITimeoutError -- connection refused, DNS, or timeout.
            log.warning("anthropic_network_error", extra={"error_type": type(err).__name__})
            raise AIProviderUnavailable("Anthropic network error") from err
        except anthropic.APIError as err:
            # Anything else the SDK raises (e.g. an unparseable response).
            log.warning("anthropic_api_error", extra={"error_type": type(err).__name__})
            raise AIProviderUnavailable("Anthropic API error") from err

        return _to_llm_turn(response)
