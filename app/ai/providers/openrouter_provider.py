"""OpenRouter adapter: the OpenAI adapter pointed at OpenRouter (MULTI_AI_PROVIDER_DESIGN.md Section 3.4).

OpenRouter speaks the OpenAI Chat Completions format -- including tool
calling (tools / assistant tool_calls with IDs / "tool" messages) -- so this
reuses OpenAIProviderClient with a different base URL. The model string is
passed through as-is and is namespaced by upstream provider, e.g.
"openai/gpt-4o-mini" or "anthropic/claude-opus-5".

What OpenRouter does differently, verified against its docs (2026-09-23) and
the installed openai SDK:
- Tool support is only a routing *preference* by default, so a request with
  tools can reach a provider that silently ignores them -- the model would
  then answer without ever touching the cart or orders.
  `provider.require_parameters` makes it a hard requirement: such a request
  fails loudly instead, and AI Settings' connection test catches it.
- An upstream failure mid-generation comes back as HTTP 200 with
  finish_reason "error" and an embedded error object -- surfaced here as
  AIProviderUnavailable, never as a (partial or blank) customer reply.
- Reasoning models return `reasoning_details` on the assistant message,
  which must be passed back unchanged on the next call in a tool loop. It
  rides in LLMTurn.provider_metadata and is replayed by the base adapter.
- error.code in error responses is the numeric status, not a short string
  code (handled in the base adapter).
"""

import logging

from app.ai.conversation import LLMTurn
from app.ai.providers.base import AIProviderUnavailable
from app.ai.providers.openai_provider import ASSISTANT_EXTRA, OpenAIProviderClient

log = logging.getLogger(__name__)


class OpenRouterProviderClient(OpenAIProviderClient):
    LABEL = "OpenRouter"
    BASE_URL = "https://openrouter.ai/api/v1"

    def _extra_request_options(self) -> dict:
        return {"extra_body": {"provider": {"require_parameters": True}}}

    def _parse_response(self, response) -> LLMTurn:
        choice = response.choices[0] if response.choices else None
        if choice is not None and choice.finish_reason == "error":
            error = getattr(choice, "error", None)
            code = error.get("code") if isinstance(error, dict) else None
            log.warning("openrouter_generation_error", extra={"error_code": code})
            raise AIProviderUnavailable("OpenRouter upstream error" + (f" {code}" if code else ""))

        turn = super()._parse_response(response)
        details = getattr(choice.message, "reasoning_details", None) if choice and choice.message else None
        if turn.function_calls and details:
            turn.provider_metadata = {ASSISTANT_EXTRA: {"reasoning_details": details}}
        return turn
