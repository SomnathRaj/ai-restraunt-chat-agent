"""The provider adapter interface and the neutral errors every adapter raises.

Each adapter catches its own SDK's exceptions internally and re-raises one of
the three types below, so app/api/chat.py never depends on any provider's
SDK (MULTI_AI_PROVIDER_DESIGN.md Section 3.5).
"""

from typing import Protocol

from app.ai.conversation import LLMTurn, Message


class AIProviderClient(Protocol):
    def generate(self, history: list[Message], tools: list[dict], system_instruction: str) -> LLMTurn: ...


class AIProviderError(Exception):
    """Base class for neutral provider failures."""


class AIProviderNotConfigured(AIProviderError):
    """The active provider has no API key set."""


class AIProviderRateLimited(AIProviderError):
    """The provider rejected the request for quota/rate-limit reasons (maps to 429)."""


class AIProviderUnavailable(AIProviderError):
    """Upstream 5xx, invalid/deprecated model, or a network-level failure (maps to 503)."""
