"""Provider-neutral conversation types (MULTI_AI_PROVIDER_DESIGN.md Section 3.1-3.2).

ChatAgent builds and consumes ONLY these shapes -- never a provider's wire
format. Each provider adapter under app/ai/providers/ translates a list of
Message objects into its own SDK's request shape, and its SDK's response
back into an LLMTurn.
"""

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    args: dict
    # Provider-assigned call ID, for providers that pair each tool result
    # with the call that requested it by ID. None when the provider has no
    # such concept.
    id: str | None = None
    # Opaque, provider-owned continuation state (e.g. Gemini's
    # thought_signature). ChatAgent never inspects it -- it only carries it
    # back to the same adapter on the next call within the same tool loop.
    provider_metadata: dict | None = None


@dataclass
class LLMTurn:
    text: str = ""
    function_calls: list[ToolCall] = field(default_factory=list)
    # Opaque, provider-owned state for the whole turn (e.g. Anthropic's
    # thinking blocks, which must be echoed back unchanged within a tool
    # loop). Carried onto the AssistantMessage untouched, never read.
    provider_metadata: dict | None = None


@dataclass
class UserMessage:
    text: str


@dataclass
class AssistantMessage:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider_metadata: dict | None = None  # from LLMTurn.provider_metadata


@dataclass
class ToolResult:
    call_id: str | None
    name: str
    response: dict


@dataclass
class ToolResultsMessage:
    """Results for every tool call from the preceding AssistantMessage, in call order."""

    results: list[ToolResult]


Message = UserMessage | AssistantMessage | ToolResultsMessage
