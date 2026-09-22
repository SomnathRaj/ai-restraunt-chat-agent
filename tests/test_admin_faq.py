import pytest
from werkzeug.security import generate_password_hash

from app.models.db import get_db
from app.services import faq_service
from app.utils.errors import AppError

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"


@pytest.fixture
def seeded_faq(app):
    with app.app_context():
        db = get_db()
        db.faq.insert_many(
            [
                {
                    "faq_id": "hours",
                    "question": "What are your opening hours?",
                    "answer": "11 AM to 11 PM.",
                    "category": "hours",
                    "keywords": ["hours", "timing"],
                    "active": True,
                },
                {
                    "faq_id": "old-promo",
                    "question": "Is the summer promo still on?",
                    "answer": "No, that promo has ended.",
                    "category": "promo",
                    "keywords": ["promo"],
                    "active": False,
                },
            ]
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


def test_create_faq_entry_generates_slug_faq_id(app):
    with app.app_context():
        entry = faq_service.create_faq_entry(
            question="Do you have parking?", answer="Yes, free parking.", category="facilities", keywords="parking, car"
        )
    assert entry["faq_id"] == "do-you-have-parking"
    assert entry["active"] is True
    assert entry["keywords"] == ["parking", "car"]


def test_create_faq_entry_dedupes_slug_on_question_collision(app):
    with app.app_context():
        first = faq_service.create_faq_entry(question="Do you deliver?", answer="Yes.", category="delivery", keywords="")
        second = faq_service.create_faq_entry(question="Do you deliver?", answer="Yes, citywide.", category="delivery", keywords="")
    assert first["faq_id"] == "do-you-deliver"
    assert second["faq_id"] == "do-you-deliver-2"


def test_create_faq_entry_missing_question_raises(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            faq_service.create_faq_entry(question="", answer="Some answer.", category="misc", keywords="")
    assert excinfo.value.code == "missing_question"


def test_create_faq_entry_missing_answer_raises(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            faq_service.create_faq_entry(question="Some question?", answer="", category="misc", keywords="")
    assert excinfo.value.code == "missing_answer"


def test_update_faq_entry_changes_fields(app, seeded_faq):
    with app.app_context():
        updated = faq_service.update_faq_entry(
            "hours", question="What time do you open?", answer="10 AM to midnight.", category="hours", keywords="hours"
        )
    assert updated["answer"] == "10 AM to midnight."
    assert updated["faq_id"] == "hours"


def test_update_faq_entry_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            faq_service.update_faq_entry("does-not-exist", question="Q", answer="A", category="", keywords="")
    assert excinfo.value.status_code == 404


def test_set_faq_entry_active_toggles(app, seeded_faq):
    with app.app_context():
        faq_service.set_faq_entry_active("hours", False)
        entry = faq_service.get_faq_entry_for_admin("hours")
    assert entry["active"] is False


def test_delete_faq_entry_removes_it(app, seeded_faq):
    with app.app_context():
        faq_service.delete_faq_entry("hours")
        db = get_db()
        assert db.faq.find_one({"faq_id": "hours"}) is None


def test_delete_faq_entry_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            faq_service.delete_faq_entry("does-not-exist")
    assert excinfo.value.status_code == 404


def test_list_all_faq_entries_includes_inactive(app, seeded_faq):
    with app.app_context():
        entries = faq_service.list_all_faq_entries()
    faq_ids = {entry["faq_id"] for entry in entries}
    assert faq_ids == {"hours", "old-promo"}


def test_list_faq_entries_page_includes_inactive_and_paginates(app, seeded_faq):
    with app.app_context():
        result = faq_service.list_faq_entries_page(page=1, page_size=1)
    assert result["total_count"] == 2
    assert result["total_pages"] == 2
    assert len(result["entries"]) == 1


def test_list_faq_entries_page_search_matches_question_answer_keywords(app, seeded_faq):
    with app.app_context():
        by_question = faq_service.list_faq_entries_page(query="opening hours")
        by_keyword = faq_service.list_faq_entries_page(query="promo")
        no_match = faq_service.list_faq_entries_page(query="nonexistent-xyz")

    assert {e["faq_id"] for e in by_question["entries"]} == {"hours"}
    assert {e["faq_id"] for e in by_keyword["entries"]} == {"old-promo"}
    assert no_match["entries"] == []
    assert no_match["total_count"] == 0


def test_deactivating_faq_entry_removes_it_from_search(app, seeded_faq):
    with app.app_context():
        faq_service.set_faq_entry_active("hours", False)
        result = faq_service.search_faq("opening hours")
    assert result["matched"] is False


# ---------------------------------------------------------------------------
# Admin HTTP route tests
# ---------------------------------------------------------------------------


def test_faq_list_requires_login(client):
    response = client.get("/admin/faq")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_faq_list_search_via_query_param(admin_client, seeded_faq):
    response = admin_client.get("/admin/faq?q=promo")
    assert response.status_code == 200
    assert b"summer promo" in response.data
    assert b"opening hours" not in response.data


def test_faq_list_shows_no_results_message_for_unmatched_search(admin_client, seeded_faq):
    response = admin_client.get("/admin/faq?q=nonexistent-xyz")
    assert response.status_code == 200
    assert b"No FAQ entries match your search." in response.data


def test_faq_list_renders_active_toggle_as_switch_and_icon_actions(admin_client, seeded_faq):
    response = admin_client.get("/admin/faq")
    assert response.status_code == 200
    html = response.data.decode()
    assert 'class="admin-switch"' in html
    assert ">Deactivate<" not in html
    assert ">Activate<" not in html
    assert ">Edit<" not in html
    assert ">Delete<" not in html
    assert 'class="admin-icon-button"' in html
    assert 'class="admin-icon-button admin-icon-button-danger"' in html

    active_toggle = html.split('action="/admin/faq/hours/toggle-active"')[1].split("</form>")[0]
    inactive_toggle = html.split('action="/admin/faq/old-promo/toggle-active"')[1].split("</form>")[0]
    assert "checked" in active_toggle
    assert "checked" not in inactive_toggle


def test_admin_can_create_faq_entry_via_form(admin_client, app):
    response = admin_client.post(
        "/admin/faq/new",
        data={
            "question": "Do you have a kids menu?",
            "answer": "Yes, ask your server for the kids menu.",
            "category": "menu",
            "keywords": "kids, children",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        entry = db.faq.find_one({"faq_id": "do-you-have-a-kids-menu"})
    assert entry is not None
    assert entry["keywords"] == ["kids", "children"]


def test_admin_faq_form_shows_error_on_missing_answer_never_500(admin_client):
    response = admin_client.post(
        "/admin/faq/new",
        data={"question": "What about allergies?", "answer": "", "category": "dietary", "keywords": ""},
    )
    assert response.status_code == 400
    assert b"Answer is required" in response.data


def test_admin_can_edit_faq_entry_via_form(admin_client, app, seeded_faq):
    response = admin_client.post(
        "/admin/faq/hours/edit",
        data={"question": "What are your opening hours?", "answer": "9 AM to midnight.", "category": "hours", "keywords": "hours"},
    )
    assert response.status_code == 302
    with app.app_context():
        entry = faq_service.get_faq_entry_for_admin("hours")
    assert entry["answer"] == "9 AM to midnight."


def test_admin_can_toggle_active_via_button(admin_client, app, seeded_faq):
    response = admin_client.post("/admin/faq/hours/toggle-active")
    assert response.status_code == 302
    with app.app_context():
        entry = faq_service.get_faq_entry_for_admin("hours")
    assert entry["active"] is False


def test_admin_can_delete_faq_entry_via_button(admin_client, app, seeded_faq):
    response = admin_client.post("/admin/faq/hours/delete")
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        assert db.faq.find_one({"faq_id": "hours"}) is None
