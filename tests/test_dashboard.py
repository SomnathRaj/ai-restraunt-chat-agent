from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from app.models.db import get_db
from app.services import dashboard_service

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"


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


def _insert_order(app, *, created_at, status="PENDING", total=100):
    with app.app_context():
        db = get_db()
        db.orders.insert_one(
            {
                "order_id": f"ORD-TEST-{created_at.isoformat()}-{status}-{total}",
                "customer_name": "Test",
                "mobile": "9876543210",
                "items": [],
                "order_notes": None,
                "subtotal": total,
                "total": total,
                "status": status,
                "payment_status": "DUE",
                "created_by": "customer",
                "created_at": created_at,
                "updated_at": created_at,
            }
        )


# ---------------------------------------------------------------------------
# resolve_date_range
# ---------------------------------------------------------------------------


def test_resolve_date_range_defaults_to_today():
    key, start, end = dashboard_service.resolve_date_range("today", None, None)
    now = datetime.now(timezone.utc)
    assert key == "today"
    assert start.date() == now.date()
    assert end == start + timedelta(days=1)


def test_resolve_date_range_week_spans_monday_to_sunday():
    key, start, end = dashboard_service.resolve_date_range("week", None, None)
    assert key == "week"
    assert start.weekday() == 0
    assert end == start + timedelta(days=7)


def test_resolve_date_range_custom_valid_bounds():
    key, start, end = dashboard_service.resolve_date_range("custom", "2026-09-01", "2026-09-05")
    assert key == "custom"
    assert start == datetime(2026, 9, 1, tzinfo=timezone.utc)
    # end is exclusive -- one day past the requested "to" date.
    assert end == datetime(2026, 9, 6, tzinfo=timezone.utc)


def test_resolve_date_range_custom_falls_back_to_today_on_bad_input():
    key, start, end = dashboard_service.resolve_date_range("custom", "not-a-date", "also-not-a-date")
    assert key == "today"

    key, start, end = dashboard_service.resolve_date_range("custom", "2026-09-05", "2026-09-01")
    assert key == "today"  # end before start is invalid

    key, start, end = dashboard_service.resolve_date_range("custom", None, None)
    assert key == "today"


def test_format_range_label_variants():
    start = datetime(2026, 9, 21, tzinfo=timezone.utc)
    end = datetime(2026, 9, 28, tzinfo=timezone.utc)
    assert dashboard_service.format_range_label("week", start, end) == "This week (2026-09-21 to 2026-09-27)"
    assert "Today" in dashboard_service.format_range_label("today", start, start + timedelta(days=1))
    assert "Custom range" in dashboard_service.format_range_label("custom", start, end)


# ---------------------------------------------------------------------------
# get_order_analytics
# ---------------------------------------------------------------------------


def test_get_order_analytics_counts_statuses_and_totals_within_range(app):
    in_range_1 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    in_range_2 = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
    out_of_range = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)

    _insert_order(app, created_at=in_range_1, status="PENDING", total=100)
    _insert_order(app, created_at=in_range_1, status="COMPLETED", total=250)
    _insert_order(app, created_at=in_range_2, status="COMPLETED", total=400)
    _insert_order(app, created_at=out_of_range, status="CANCEL", total=999)

    start = datetime(2026, 9, 21, tzinfo=timezone.utc)
    end = datetime(2026, 9, 23, tzinfo=timezone.utc)

    with app.app_context():
        stats = dashboard_service.get_order_analytics(start, end)

    assert stats["total_orders"] == 3
    assert stats["total_sales"] == 750
    assert stats["status_counts"] == {"PENDING": 1, "PROCESSING_FOOD": 0, "COMPLETED": 2, "CANCEL": 0}
    assert stats["daily_sales"] == [
        {"date": "2026-09-21", "total": 350},
        {"date": "2026-09-22", "total": 400},
    ]


def test_get_order_analytics_empty_range_is_zero_filled(app):
    start = datetime(2026, 9, 21, tzinfo=timezone.utc)
    end = datetime(2026, 9, 23, tzinfo=timezone.utc)

    with app.app_context():
        stats = dashboard_service.get_order_analytics(start, end)

    assert stats["total_orders"] == 0
    assert stats["total_sales"] == 0
    assert stats["status_counts"] == {"PENDING": 0, "PROCESSING_FOOD": 0, "COMPLETED": 0, "CANCEL": 0}
    assert stats["daily_sales"] == [
        {"date": "2026-09-21", "total": 0},
        {"date": "2026-09-22", "total": 0},
    ]


# ---------------------------------------------------------------------------
# get_customer_count / get_menu_availability_counts / get_veg_nonveg_counts
# ---------------------------------------------------------------------------


def test_get_customer_count_only_counts_sessions_in_range(app):
    # updated_at is kept "now" (as a real, currently-active session would
    # have) since chat_sessions has a TTL index on it -- mongomock actively
    # expires documents whose updated_at is older than SESSION_TTL_SECONDS
    # relative to the real wall clock. Only created_at varies, since that's
    # what get_customer_count actually filters on.
    now = datetime.now(timezone.utc)
    with app.app_context():
        db = get_db()
        db.chat_sessions.insert_many(
            [
                {"session_id": "s1", "created_at": datetime(2026, 9, 21, tzinfo=timezone.utc), "updated_at": now},
                {"session_id": "s2", "created_at": datetime(2026, 9, 22, tzinfo=timezone.utc), "updated_at": now},
                {"session_id": "s3", "created_at": datetime(2026, 9, 25, tzinfo=timezone.utc), "updated_at": now},
            ]
        )

    start = datetime(2026, 9, 21, tzinfo=timezone.utc)
    end = datetime(2026, 9, 23, tzinfo=timezone.utc)
    with app.app_context():
        count = dashboard_service.get_customer_count(start, end)
    assert count == 2


def test_get_menu_availability_counts_active_only(app):
    with app.app_context():
        db = get_db()
        db.menu.insert_many(
            [
                {"item_id": "a", "name": "A", "category": "X", "price": 10, "availability": True, "is_veg": True, "active": True},
                {"item_id": "b", "name": "B", "category": "X", "price": 10, "availability": False, "is_veg": True, "active": True},
                {"item_id": "c", "name": "C", "category": "X", "price": 10, "availability": False, "is_veg": True, "active": False},
            ]
        )
        counts = dashboard_service.get_menu_availability_counts()
    # 'c' is inactive -- must not be counted either way.
    assert counts == {"available": 1, "unavailable": 1}


def test_get_veg_nonveg_counts_active_only(app):
    with app.app_context():
        db = get_db()
        db.menu.insert_many(
            [
                {"item_id": "a", "name": "A", "category": "X", "price": 10, "availability": True, "is_veg": True, "active": True},
                {"item_id": "b", "name": "B", "category": "X", "price": 10, "availability": True, "is_veg": False, "active": True},
                {"item_id": "c", "name": "C", "category": "X", "price": 10, "availability": True, "is_veg": False, "active": False},
            ]
        )
        counts = dashboard_service.get_veg_nonveg_counts()
    assert counts == {"veg": 1, "nonveg": 1}


# ---------------------------------------------------------------------------
# HTTP-level
# ---------------------------------------------------------------------------


def test_dashboard_requires_login(client):
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_dashboard_renders_default_today_view(admin_client):
    response = admin_client.get("/admin/")
    assert response.status_code == 200
    assert b"Total Orders" in response.data
    assert b"Customers Connected" in response.data
    assert b"Today (" in response.data


def test_dashboard_renders_week_view(admin_client):
    response = admin_client.get("/admin/?range=week")
    assert response.status_code == 200
    assert b"This week (" in response.data


def test_dashboard_renders_custom_range_view(admin_client):
    response = admin_client.get("/admin/?range=custom&start=2026-09-01&end=2026-09-10")
    assert response.status_code == 200
    assert b"Custom range (2026-09-01 to 2026-09-10)" in response.data


def test_dashboard_shows_correct_totals_for_selected_range(admin_client, app):
    _insert_order(app, created_at=datetime(2026, 9, 5, tzinfo=timezone.utc), status="COMPLETED", total=350)
    response = admin_client.get("/admin/?range=custom&start=2026-09-01&end=2026-09-10")
    assert response.status_code == 200
    assert b"350.00" in response.data
