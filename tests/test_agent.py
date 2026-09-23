import pytest

from app.ai.agent import FRIENDLY_FALLBACK_MESSAGE, ChatAgent
from app.ai.conversation import AssistantMessage, ToolResultsMessage, UserMessage
from app.ai.tool_executor import ToolExecutor, UnknownToolError
from app.models.db import get_db
from app.services import cart_service
from tests.conftest import FakeAIClient, LLMTurn, ToolCall


def test_tool_executor_rejects_unknown_tool_name(app):
    with app.app_context():
        with pytest.raises(UnknownToolError):
            ToolExecutor().execute("delete_everything", {}, "s1")


def test_tool_executor_allow_list_is_exactly_the_declared_tools():
    from app.ai.tool_executor import _HANDLERS
    from app.ai.tool_schemas import TOOL_SCHEMAS

    declared_names = {schema["name"] for schema in TOOL_SCHEMAS}
    assert set(_HANDLERS.keys()) == declared_names


@pytest.fixture
def seeded_menu(app):
    with app.app_context():
        db = get_db()
        db.menu.insert_one(
            {
                "item_id": "coke",
                "name": "Coke",
                "description": "Chilled soft drink",
                "category": "Beverage",
                "price": 60,
                "availability": True,
                "tags": ["drink"],
                "active": True,
            }
        )
    return app


def test_chat_agent_returns_text_immediately_when_no_tool_calls(app):
    fake = FakeAIClient([LLMTurn(text="Hello! What would you like today?")])
    with app.app_context():
        reply = ChatAgent(client=fake).handle_message("s1", "Hi")
    assert reply == "Hello! What would you like today?"
    assert len(fake.calls) == 1


def test_chat_agent_saves_turn_to_session_history(app):
    fake = FakeAIClient([LLMTurn(text="Sure!")])
    with app.app_context():
        ChatAgent(client=fake).handle_message("s1", "Show me the menu")
        from app.services import session_service

        session = session_service.get_or_create("s1")
    history = session["conversation_context"]["history"]
    assert history[0] == {"role": "user", "text": "Show me the menu", "ts": history[0]["ts"]}
    assert history[1]["role"] == "model"
    assert history[1]["text"] == "Sure!"


def test_clear_cart_tool_empties_items_and_order_notes_in_one_call(seeded_menu):
    # Regression test (Phase 9): before clear_cart existed as a declared
    # tool, "cancel my order" only worked by Gemini improvising get_cart +
    # remove_from_cart per item -- which silently left order_notes behind
    # and could exceed the iteration cap for a larger cart.
    fake = FakeAIClient(
        [
            LLMTurn(function_calls=[ToolCall(name="clear_cart", args={})]),
            LLMTurn(text="Your order has been cancelled."),
        ]
    )
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 2)
        cart_service.set_order_notes("s1", "No onion anywhere")
        reply = ChatAgent(client=fake).handle_message("s1", "Cancel my whole order")
        cart = cart_service.get_cart("s1")

    assert reply == "Your order has been cancelled."
    assert cart == {"items": [], "order_notes": None, "subtotal": 0, "total": 0}


def test_chat_agent_executes_multiple_tool_calls_in_one_turn(seeded_menu):
    fake = FakeAIClient(
        [
            LLMTurn(
                function_calls=[
                    ToolCall(name="add_to_cart", args={"item_id": "coke", "quantity": 2}),
                    ToolCall(name="set_item_instructions", args={"item_id": "coke", "instructions": "less ice"}),
                ]
            ),
            LLMTurn(text="Added 2 Cokes with less ice!"),
        ]
    )
    with seeded_menu.app_context():
        reply = ChatAgent(client=fake).handle_message("s1", "Two cokes, less ice please")
        cart = cart_service.get_cart("s1")

    assert reply == "Added 2 Cokes with less ice!"
    assert cart["items"] == [
        {"item_id": "coke", "name": "Coke", "price": 60, "quantity": 2, "instructions": "less ice"}
    ]
    assert len(fake.calls) == 2


def test_chat_agent_stops_at_max_iterations_and_returns_fallback(seeded_menu):
    # Every scripted turn keeps requesting another tool call, so the loop
    # never naturally terminates -- it must stop at max_iterations, not hang.
    endless_calls = [LLMTurn(function_calls=[ToolCall(name="get_cart", args={})]) for _ in range(10)]
    fake = FakeAIClient(endless_calls)
    with seeded_menu.app_context():
        reply = ChatAgent(client=fake, max_iterations=3).handle_message("s1", "loop forever")
    assert reply == FRIENDLY_FALLBACK_MESSAGE
    assert len(fake.calls) == 3


def test_chat_agent_max_iterations_defaults_from_config(app):
    fake = FakeAIClient([LLMTurn(text="ok")])
    with app.app_context():
        agent = ChatAgent(client=fake)
        assert agent.max_iterations == app.config["TOOL_LOOP_MAX_ITERATIONS"]


def test_chat_agent_recovers_from_unknown_tool_without_crashing(app):
    fake = FakeAIClient(
        [
            LLMTurn(function_calls=[ToolCall(name="not_a_real_tool", args={})]),
            LLMTurn(text="Sorry, let me help another way."),
        ]
    )
    with app.app_context():
        reply = ChatAgent(client=fake).handle_message("s1", "do something weird")
    assert reply == "Sorry, let me help another way."
    assert len(fake.calls) == 2


def test_chat_agent_passes_provider_metadata_back_untouched_on_next_call(app):
    # ChatAgent never inspects provider_metadata -- it just hands it back to
    # the adapter on the next call in the same loop (e.g. Gemini's
    # thought_signature). The adapter-side translation is tested in
    # tests/test_gemini_provider.py.
    metadata = {"thought_signature": b"opaque-token"}
    fake = FakeAIClient(
        [
            LLMTurn(function_calls=[ToolCall(name="get_cart", args={}, provider_metadata=metadata)]),
            LLMTurn(text="ok"),
        ]
    )
    with app.app_context():
        ChatAgent(client=fake).handle_message("s1", "show my cart")

    second_call_history = fake.calls[1]["history"]
    assistant_turn = next(m for m in second_call_history if isinstance(m, AssistantMessage))
    assert assistant_turn.tool_calls[0].provider_metadata is metadata


def test_chat_agent_builds_neutral_conversation_from_history_and_tool_results(seeded_menu):
    fake = FakeAIClient(
        [
            LLMTurn(text="Hi there!"),
            LLMTurn(function_calls=[ToolCall(name="check_item_availability", args={"item_id": "coke"}, id="call-1")]),
            LLMTurn(text="Yes, Coke is available."),
        ]
    )
    with seeded_menu.app_context():
        ChatAgent(client=fake).handle_message("s1", "Hello")
        ChatAgent(client=fake).handle_message("s1", "Is Coke available?")

    history = fake.calls[2]["history"]
    # Persisted "model" turns come back as neutral assistant messages.
    assert history[:3] == [
        UserMessage(text="Hello"),
        AssistantMessage(text="Hi there!"),
        UserMessage(text="Is Coke available?"),
    ]
    assert isinstance(history[3], AssistantMessage)
    assert history[3].tool_calls[0].name == "check_item_availability"
    results_message = history[4]
    assert isinstance(results_message, ToolResultsMessage)
    assert results_message.results[0].call_id == "call-1"
    assert results_message.results[0].name == "check_item_availability"
    # A bare bool result is wrapped by _json_safe, same as before the refactor.
    assert results_message.results[0].response == {"result": True}


def test_chat_agent_passes_neutral_tool_schemas_to_the_client(app):
    from app.ai.tool_schemas import TOOL_SCHEMAS

    fake = FakeAIClient([LLMTurn(text="ok")])
    with app.app_context():
        ChatAgent(client=fake).handle_message("s1", "hi")
    assert fake.calls[0]["tools"] is TOOL_SCHEMAS


def test_tool_executor_reports_missing_required_arguments_instead_of_crashing(app):
    # A model can omit a required argument, or an adapter can pass {} for
    # unparseable arguments -- either way the model gets a structured error.
    with app.app_context():
        result = ToolExecutor().execute("add_to_cart", {"item_id": "coke"}, "s1")
    assert result == {"error": "invalid_arguments", "message": "Missing required argument(s): quantity"}


def test_chat_agent_carries_turn_level_provider_metadata_onto_the_assistant_message(app):
    # e.g. Anthropic's raw content (thinking blocks) -- opaque to ChatAgent.
    metadata = {"content": ["opaque"]}
    fake = FakeAIClient(
        [
            LLMTurn(function_calls=[ToolCall(name="get_cart", args={}, id="t1")], provider_metadata=metadata),
            LLMTurn(text="ok"),
        ]
    )
    with app.app_context():
        ChatAgent(client=fake).handle_message("s1", "cart")
    assistant = next(m for m in fake.calls[1]["history"] if isinstance(m, AssistantMessage))
    assert assistant.provider_metadata is metadata
