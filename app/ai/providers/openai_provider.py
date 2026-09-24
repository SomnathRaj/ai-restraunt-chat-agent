"""OpenAI adapter: neutral conversation/tool schemas <-> the Chat Completions API.

Chat Completions (not the newer Responses API) on purpose: it's the
OpenAI-compatible wire format OpenRouter also speaks, so that adapter can
reuse this one with a different BASE_URL (MULTI_AI_PROVIDER_DESIGN.md
Section 3.4).

Differences from Gemini that this adapter owns:
- Every tool call has an ID, and each result must go back as its own
  {"role": "tool", "tool_call_id": ...} message. ToolCall.id and
  ToolResult.call_id carry it through ChatAgent untouched.
- Tool-call arguments arrive as a JSON string, and results must be sent as
  strings -- both are (de)serialized here.
- The system prompt is the first message, not a separate config field.
- No thought_signature equivalent, so provider_metadata stays None.

As with Gemini, tools are schema-only; the model can only request a call,
and every call goes through app/ai/tool_executor.py.
"""

import json
import logging

import openai

from app.ai.conversation import AssistantMessage, LLMTurn, Message, ToolCall, ToolResultsMessage, UserMessage
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable

log = logging.getLogger(__name__)

# The SDK defaults (10-minute timeout, 2 retries) are far too patient for a
# customer waiting on a chat reply.
REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 1

# LLMTurn/AssistantMessage provider_metadata key: extra fields a subclass
# needs replayed verbatim on its assistant tool-call message (e.g.
# OpenRouter's reasoning_details). Plain OpenAI never sets it.
ASSISTANT_EXTRA = "assistant_extra"


def _to_messages(history: list[Message], system_instruction: str) -> list[dict]:
    messages = [{"role": "system", "content": system_instruction}] if system_instruction else []
    for message in history:
        if isinstance(message, UserMessage):
            messages.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            if not message.tool_calls:
                messages.append({"role": "assistant", "content": message.text})
                continue
            messages.append(
                {
                    "role": "assistant",
                    "content": message.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.args)},
                        }
                        for call in message.tool_calls
                    ],
                    **(message.provider_metadata or {}).get(ASSISTANT_EXTRA, {}),
                }
            )
        elif isinstance(message, ToolResultsMessage):
            for result in message.results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.call_id,
                        # default=str: service results can include datetimes.
                        "content": json.dumps(result.response, default=str),
                    }
                )
    return messages


def _to_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["parameters"]},
        }
        for tool in tools
    ]


def _parse_arguments(raw: str | None, tool_name: str) -> dict:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        # Rare, but possible without strict mode. An empty dict makes the
        # executor return a structured invalid_arguments result the model
        # can recover from, instead of failing the whole customer message.
        log.warning("openai_malformed_tool_arguments", extra={"tool_name": tool_name})
        return {}
    return args if isinstance(args, dict) else {}


def _is_out_of_credit(err: openai.RateLimitError) -> bool:
    # Seen live 2026-09-23: OpenAI now reports no credits as
    # type="insufficient_quota", code="credit_balance_exhausted". Older
    # responses put "insufficient_quota" in `code` -- accept both.
    return "insufficient_quota" in (getattr(err, "type", None), err.code)


def _to_llm_turn(response) -> LLMTurn:
    choice = response.choices[0] if response.choices else None
    if choice is None or choice.message is None:
        return LLMTurn()

    message = choice.message
    turn = LLMTurn(text=message.content or message.refusal or "")
    for call in message.tool_calls or []:
        if getattr(call, "type", "function") != "function":
            continue  # only function tools are ever declared
        turn.function_calls.append(
            ToolCall(
                name=call.function.name,
                args=_parse_arguments(call.function.arguments, call.function.name),
                id=call.id,
            )
        )
    return turn


class OpenAIProviderClient:
    """Owns the actual SDK call. app/ai/agent.py depends on the neutral interface, not the SDK.

    The API key and model come from the admin-managed ai_providers record
    via app/ai/providers/registry.py. `client` injects a stand-in SDK client
    for tests. Subclasses for OpenAI-compatible providers override LABEL
    (used in logs and admin-facing error summaries), BASE_URL, and if
    needed the _extra_request_options() / _parse_response() hooks.
    """

    LABEL = "OpenAI"
    BASE_URL: str | None = None

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None):
        self._api_key = api_key
        self._model = model
        self._client = client

    def _create_client(self):
        kwargs = {"api_key": self._api_key, "timeout": REQUEST_TIMEOUT_SECONDS, "max_retries": MAX_RETRIES}
        if self.BASE_URL:
            kwargs["base_url"] = self.BASE_URL
        return openai.OpenAI(**kwargs)

    def _extra_request_options(self) -> dict:
        """Extra keyword arguments for chat.completions.create() (e.g. extra_body)."""
        return {}

    def _parse_response(self, response) -> LLMTurn:
        return _to_llm_turn(response)

    def generate(self, history: list[Message], tools: list[dict], system_instruction: str) -> LLMTurn:
        if self._client is None and not self._api_key:
            raise AIProviderNotConfigured(f"no {self.LABEL} API key")
        if not self._model:
            raise AIProviderNotConfigured(f"no {self.LABEL} model")
        # Built once per adapter, then reused, so every round-trip of one
        # message's tool loop shares a single HTTPS connection. The
        # registry builds a fresh adapter per customer message, so
        # nothing (including the key) outlives that message.
        if self._client is None:
            self._client = self._create_client()
        client = self._client

        request = {"model": self._model, "messages": _to_messages(history, system_instruction)}
        if tools:
            request["tools"] = _to_tools(tools)
        request.update(self._extra_request_options())

        try:
            response = client.chat.completions.create(**request)
        except openai.RateLimitError as err:
            log.warning("openai_api_error", extra={"provider": self.LABEL, "status_code": 429, "error_code": err.code})
            if _is_out_of_credit(err):
                # Out of credit, not a burst limit -- retrying won't help.
                raise AIProviderUnavailable(f"{self.LABEL} API error 429 insufficient_quota") from err
            raise AIProviderRateLimited(f"{self.LABEL} rate limit exceeded") from err
        except openai.APIStatusError as err:
            # 401 bad key, 403, 404 unknown/deprecated model, 400, 5xx. Only
            # the status and the short error code (e.g. "model_not_found")
            # are kept -- never the response body.
            log.warning(
                "openai_api_error", extra={"provider": self.LABEL, "status_code": err.status_code, "error_code": err.code}
            )
            # OpenAI's codes are short words ("model_not_found"); OpenRouter's
            # is just the numeric status again (the SDK hands it over as a
            # string, e.g. "402"), which would only repeat it.
            code = err.code if err.code is not None and not str(err.code).isdigit() else None
            summary = f"{self.LABEL} API error {err.status_code}" + (f" {code}" if code else "")
            raise AIProviderUnavailable(summary) from err
        except openai.APIConnectionError as err:
            # Includes APITimeoutError -- connection refused, DNS, or timeout.
            log.warning("openai_network_error", extra={"provider": self.LABEL, "error_type": type(err).__name__})
            raise AIProviderUnavailable(f"{self.LABEL} network error") from err
        except openai.APIError as err:
            # Anything else the SDK raises (e.g. an unparseable response).
            log.warning("openai_api_error", extra={"provider": self.LABEL, "error_type": type(err).__name__})
            raise AIProviderUnavailable(f"{self.LABEL} API error") from err

        return self._parse_response(response)
