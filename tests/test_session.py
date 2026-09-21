from app.models.db import get_db
from app.services import session_service


def test_get_or_create_creates_default_session(app):
    with app.app_context():
        session = session_service.get_or_create("s1")
    assert session["session_id"] == "s1"
    assert session["cart"] == {"items": [], "order_notes": None}
    assert session["customer_name"] is None
    assert session["mobile"] is None
    assert session["conversation_context"]["history"] == []
    assert session["conversation_context"]["checkout_flags"] == {
        "instructions_prompted": False,
        "instructions_declined": False,
    }


def test_get_or_create_is_idempotent_and_returns_same_doc(app):
    with app.app_context():
        first = session_service.get_or_create("s1")
        session_service.set_customer_info("s1", "Somnath", "9876543210")
        second = session_service.get_or_create("s1")
    # Second call must not reset a session that already has customer info.
    assert first["session_id"] == second["session_id"]
    assert second["customer_name"] == "Somnath"

    with app.app_context():
        db = get_db()
        assert db.chat_sessions.count_documents({"session_id": "s1"}) == 1


def test_append_turn_stores_history_and_bumps_updated_at(app):
    with app.app_context():
        before = session_service.get_or_create("s1")
        session_service.append_turn("s1", "Show me the menu", "Here you go!")
        after = session_service.get_or_create("s1")

    history = after["conversation_context"]["history"]
    assert [h["role"] for h in history] == ["user", "model"]
    assert history[0]["text"] == "Show me the menu"
    assert history[1]["text"] == "Here you go!"
    # MongoDB datetimes only have millisecond precision, so on a fast test
    # run these can legitimately tie -- >= is the real invariant (never
    # goes backwards), not strict ordering.
    assert after["updated_at"] >= before["updated_at"]


def test_append_turn_trims_history_to_configured_max(app):
    with app.app_context():
        for i in range(15):
            session_service.append_turn("s1", f"message {i}", f"reply {i}")
        session = session_service.get_or_create("s1")

    max_messages = app.config["HISTORY_MAX_MESSAGES"]
    assert len(session["conversation_context"]["history"]) == max_messages
    # Oldest turns should have been dropped -- the most recent turn survives.
    assert session["conversation_context"]["history"][-1]["text"] == "reply 14"


def test_set_customer_info(app):
    with app.app_context():
        session_service.set_customer_info("s1", "Somnath", "9876543210")
        session = session_service.get_or_create("s1")
    assert session["customer_name"] == "Somnath"
    assert session["mobile"] == "9876543210"


def test_mark_instructions_prompted_and_declined(app):
    with app.app_context():
        session_service.mark_instructions_prompted("s1")
        session_service.mark_instructions_declined("s1")
        session = session_service.get_or_create("s1")
    flags = session["conversation_context"]["checkout_flags"]
    assert flags["instructions_prompted"] is True
    assert flags["instructions_declined"] is True


def test_sessions_are_isolated(app):
    with app.app_context():
        session_service.set_customer_info("session-a", "Alice", "9876543210")
        session_b = session_service.get_or_create("session-b")
    assert session_b["customer_name"] is None


def test_chat_sessions_ttl_index_matches_config(app):
    with app.app_context():
        db = get_db()  # triggers ensure_indexes() lazily
        indexes = db.chat_sessions.index_information()
        ttl_indexes = [info for info in indexes.values() if "expireAfterSeconds" in info]

    assert len(ttl_indexes) == 1
    assert ttl_indexes[0]["expireAfterSeconds"] == app.config["SESSION_TTL_SECONDS"]
    assert ttl_indexes[0]["key"] == [("updated_at", 1)]
