from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from app.models.db import get_db
from app.services import cart_service, order_service, session_service
from app.utils.errors import AppError

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"


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
                "is_veg": True,
                "tags": ["drink"],
                "active": True,
            }
        )
    return app


@pytest.fixture
def admin_client(app, client):
    with app.app_context():
        db = get_db()
        db.users.insert_one(
            {
                "user_id": "usr_test",
                "email": ADMIN_EMAIL,
                "password_hash": generate_password_hash(ADMIN_PASSWORD),
                "name": "Admin",
                "role": "admin",
                "active": True,
            }
        )
    client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return client


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


def test_list_sessions_page_excludes_conversation_context_and_cart(app):
    with app.app_context():
        session_service.append_turn("s1", "a secret question", "a secret answer")
        session_service.set_customer_info("s1", "Somnath", "9876543210")
        result = session_service.list_sessions_page()
    assert len(result["sessions"]) == 1
    session = result["sessions"][0]
    assert set(session.keys()) == {"session_id", "customer_name", "mobile", "updated_at"}
    assert "conversation_context" not in session
    assert "cart" not in session


def test_list_sessions_page_most_recently_active_first(app):
    with app.app_context():
        session_service.get_or_create("s-old")
        db = get_db()
        db.chat_sessions.update_one(
            {"session_id": "s-old"}, {"$set": {"updated_at": datetime.now(timezone.utc) - timedelta(hours=1)}}
        )
        session_service.get_or_create("s-new")
        result = session_service.list_sessions_page()
    assert [s["session_id"] for s in result["sessions"]] == ["s-new", "s-old"]


def test_list_sessions_page_pagination(app):
    with app.app_context():
        db = get_db()
        now = datetime.now(timezone.utc)
        for i, session_id in enumerate(["s1", "s2", "s3"]):
            session_service.get_or_create(session_id)
            db.chat_sessions.update_one(
                {"session_id": session_id}, {"$set": {"updated_at": now - timedelta(minutes=i)}}
            )
        first_page = session_service.list_sessions_page(page=1, page_size=2)
        second_page = session_service.list_sessions_page(page=2, page_size=2)
    assert [s["session_id"] for s in first_page["sessions"]] == ["s1", "s2"]
    assert first_page["total_pages"] == 2
    assert [s["session_id"] for s in second_page["sessions"]] == ["s3"]


def test_list_sessions_page_search_by_name_or_mobile(app):
    with app.app_context():
        session_service.set_customer_info("s1", "Alice Smith", "9876543210")
        session_service.set_customer_info("s2", "Bob Jones", "9876543211")
        by_name = session_service.list_sessions_page(query="alice")
        by_mobile = session_service.list_sessions_page(query="43211")
    assert [s["session_id"] for s in by_name["sessions"]] == ["s1"]
    assert [s["session_id"] for s in by_mobile["sessions"]] == ["s2"]


def test_list_sessions_page_search_never_matches_message_content(app):
    with app.app_context():
        session_service.append_turn("s1", "a very unique secret phrase", "reply")
        result = session_service.list_sessions_page(query="unique secret phrase")
    assert result["sessions"] == []
    assert result["total_count"] == 0


def test_get_session_history_returns_full_history(app):
    with app.app_context():
        session_service.append_turn("s1", "Show me the menu", "Here you go!")
        session_service.set_customer_info("s1", "Somnath", "9876543210")
        session = session_service.get_session_history("s1")
    assert session["customer_name"] == "Somnath"
    assert session["mobile"] == "9876543210"
    assert [turn["role"] for turn in session["history"]] == ["user", "model"]
    assert session["history"][0]["text"] == "Show me the menu"


def test_get_session_history_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            session_service.get_session_history("does-not-exist")
    assert excinfo.value.status_code == 404


def test_get_session_history_never_auto_creates(app):
    with app.app_context():
        with pytest.raises(AppError):
            session_service.get_session_history("never-existed")
        db = get_db()
        assert db.chat_sessions.find_one({"session_id": "never-existed"}) is None


def test_delete_session_removes_it(app):
    with app.app_context():
        session_service.get_or_create("s1")
        session_service.delete_session("s1")
        db = get_db()
        assert db.chat_sessions.find_one({"session_id": "s1"}) is None


def test_delete_session_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            session_service.delete_session("does-not-exist")
    assert excinfo.value.status_code == 404


def test_delete_session_leaves_its_order_intact(app, seeded_menu):
    with app.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        session_service.mark_instructions_prompted("s1")
        order = order_service.create_order("s1", "Somnath", "9876543210")

        session_service.delete_session("s1")

        db = get_db()
        assert db.chat_sessions.find_one({"session_id": "s1"}) is None
        stored_order = order_service.get_order_status(order["order_id"])
    assert stored_order["order_id"] == order["order_id"]


# ---------------------------------------------------------------------------
# Admin HTTP route tests
# ---------------------------------------------------------------------------


def test_sessions_list_requires_login(client):
    response = client.get("/admin/sessions")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_sessions_list_search_via_query_param(admin_client, app):
    with app.app_context():
        session_service.set_customer_info("s1", "Somnath", "9876543210")
        session_service.set_customer_info("s2", "Someone Else", "9876543299")

    response = admin_client.get("/admin/sessions?q=Somnath")
    assert response.status_code == 200
    assert b"Somnath" in response.data
    assert b"Someone Else" not in response.data


def test_sessions_list_shows_no_results_message_for_unmatched_search(admin_client, app):
    with app.app_context():
        session_service.get_or_create("s1")

    response = admin_client.get("/admin/sessions?q=nonexistent-xyz")
    assert response.status_code == 200
    assert b"No chat sessions match your search." in response.data


def test_sessions_list_shows_only_name_mobile_timestamp(admin_client, app):
    with app.app_context():
        session_service.append_turn("s1", "a very secret message", "a very secret reply")
        session_service.set_customer_info("s1", "Somnath", "9876543210")

    response = admin_client.get("/admin/sessions")
    assert response.status_code == 200
    assert b"Somnath" in response.data
    assert b"9876543210" in response.data
    assert b"a very secret message" not in response.data
    assert b"a very secret reply" not in response.data


def test_sessions_list_shows_anonymous_placeholder_when_no_customer_info(admin_client, app):
    with app.app_context():
        session_service.get_or_create("s-anon")

    response = admin_client.get("/admin/sessions")
    assert response.status_code == 200
    assert b"Anonymous" in response.data


def test_sessions_list_view_and_delete_are_icon_buttons_without_text_labels(admin_client, app):
    with app.app_context():
        session_service.get_or_create("s1")

    response = admin_client.get("/admin/sessions")
    assert response.status_code == 200
    html = response.data.decode()
    assert ">View<" not in html
    assert ">Delete<" not in html
    assert 'class="admin-icon-button"' in html
    assert 'class="admin-icon-button admin-icon-button-danger"' in html


def test_session_detail_shows_full_transcript(admin_client, app):
    with app.app_context():
        session_service.append_turn("s1", "Show me the menu", "Here you go!")
        session_service.set_customer_info("s1", "Somnath", "9876543210")

    response = admin_client.get("/admin/sessions/s1")
    assert response.status_code == 200
    assert b"Show me the menu" in response.data
    assert b"Here you go!" in response.data


def test_session_detail_missing_returns_404(admin_client):
    response = admin_client.get("/admin/sessions/does-not-exist")
    assert response.status_code == 404


def test_admin_can_delete_session_via_button(admin_client, app):
    with app.app_context():
        session_service.get_or_create("s1")

    response = admin_client.post("/admin/sessions/s1/delete")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/sessions"
    with app.app_context():
        db = get_db()
        assert db.chat_sessions.find_one({"session_id": "s1"}) is None
