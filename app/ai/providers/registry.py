"""Resolves the admin-selected active provider into an adapter (MULTI_AI_PROVIDER_DESIGN.md Section 3.3).

Called fresh for every customer message -- no caching of the client or the
decrypted key -- so an admin's provider switch applies to the very next
message with no restart (Section 6).
"""

import logging

from app.ai.conversation import UserMessage
from app.ai.providers.anthropic_provider import AnthropicProviderClient
from app.ai.providers.base import (
    AIProviderClient,
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderRateLimited,
    AIProviderUnavailable,
)
from app.ai.providers.gemini import GeminiProviderClient
from app.ai.providers.openai_provider import OpenAIProviderClient
from app.ai.providers.openrouter_provider import OpenRouterProviderClient
from app.ai.tool_schemas import TOOL_SCHEMAS
from app.services import ai_provider_service
from app.utils.credentials_crypto import EncryptionKeyUnavailable, StoredKeyUnreadable

log = logging.getLogger(__name__)

# One entry per provider with a working adapter. Must match the providers
# marked implemented in ai_provider_service.PROVIDERS.
ADAPTERS = {
    "gemini": GeminiProviderClient,
    "openai": OpenAIProviderClient,
    "anthropic": AnthropicProviderClient,
    "openrouter": OpenRouterProviderClient,
}

_TEST_PROMPT = "Reply with the single word OK."


def build_client(provider: str, *, api_key: str, model: str) -> AIProviderClient:
    return ADAPTERS[provider](api_key=api_key, model=model)


def get_active_ai_client() -> AIProviderClient:
    record = ai_provider_service.get_active()
    if record is None:
        raise AIProviderNotConfigured("no active AI provider")
    try:
        api_key = ai_provider_service.decrypt_api_key(record)
    except (EncryptionKeyUnavailable, StoredKeyUnreadable) as err:
        # A server-side misconfiguration (Appendix A.1), not a missing
        # setup step -- customers get the same "trouble reaching our AI
        # service" reply as an upstream outage.
        log.warning("ai_credentials_unreadable", extra={"provider": record["provider"], "reason": type(err).__name__})
        raise AIProviderUnavailable("active provider's API key can't be decrypted") from None
    if not api_key or not record.get("model"):
        raise AIProviderNotConfigured(f"{record['provider']} has no API key or model")
    return build_client(record["provider"], api_key=api_key, model=record["model"])


_TEST_FAILURE_MESSAGES = {
    AIProviderNotConfigured: "No API key was provided.",
    AIProviderRateLimited: "The provider is rate-limiting requests right now. The key may be fine; try again shortly.",
    # Covers a wrong key (401/403), an unknown/deprecated model (404), and an
    # account out of credit (Anthropic reports that as a plain 400).
    AIProviderUnavailable: "Couldn't get a reply. Check the API key, the model name, and that the account has credit.",
}


def check_connection(provider: str, *, api_key: str, model: str) -> tuple[bool, str]:
    """Send one trivial prompt; return (ok, admin-facing message). Never raises for provider errors.

    Sends the real tool schemas, so a model (or an OpenRouter route) that
    can't do tool calling fails here instead of failing customers later. A
    reply that requests a tool counts as a pass -- tool calling works.

    The message may include the adapter's short error summary (e.g. "Gemini
    API error 404") -- never a response body, and never the key.
    """
    try:
        turn = build_client(provider, api_key=api_key, model=model).generate(
            [UserMessage(text=_TEST_PROMPT)], tools=TOOL_SCHEMAS, system_instruction="You are a connection test."
        )
    except AIProviderError as err:
        return False, f"{_TEST_FAILURE_MESSAGES.get(type(err), 'The provider returned an error.')} ({err})"
    if not turn.text.strip() and not turn.function_calls:
        return False, "The provider responded, but with an empty reply."
    return True, f"Connected. {model} replied."
