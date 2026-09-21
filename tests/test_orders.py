import threading

import pytest

from app.models.db import get_db
from app.services import cart_service, order_service, session_service
from app.utils.errors import AppError


@pytest.fixture
def seeded_menu(app):
    with app.app_context():
        db = get_db()
        db.menu.insert_many(
            [
                {
                    "item_id": "chicken-biryani",
                    "name": "Chicken Biryani",
                    "description": "Spiced rice with chicken",
                    "category": "Main Course",
                    "price": 280,
                    "availability": True,
                    "tags": ["chicken"],
                    "active": True,
                },
                {
                    "item_id": "coke",
                    "name": "Coke",
                    "description": "Chilled soft drink",
                    "category": "Beverage",
                    "price": 60,
                    "availability": True,
                    "tags": ["drink"],
                    "active": True,
                },
            ]
        )
    return app


def _confirm_ready_cart(app, session_id="s1"):
    """Add an item and satisfy the instructions-prompted gate, without ordering."""
    with app.app_context():
        cart_service.add_to_cart(session_id, "chicken-biryani", 1)
        cart_service.add_to_cart(session_id, "coke", 2)
        session_service.mark_instructions_prompted(session_id)


# ---------------------------------------------------------------- preview_order


def test_preview_order_computes_totals(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        preview = order_service.preview_order("s1")
    assert preview["subtotal"] == preview["total"] == 400
    assert preview["unavailable_items"] == []
    assert {item["item_id"] for item in preview["items"]} == {"chicken-biryani", "coke"}


def test_preview_order_uses_live_price_not_stale_cart_price(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        db = get_db()
        db.menu.update_one({"item_id": "coke"}, {"$set": {"price": 90}})
        preview = order_service.preview_order("s1")
    coke_line = next(item for item in preview["items"] if item["item_id"] == "coke")
    assert coke_line["price"] == 90
    assert coke_line["total"] == 180
    assert preview["subtotal"] == 280 + 180


def test_preview_order_flags_unavailable_without_raising(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        db = get_db()
        db.menu.update_one({"item_id": "coke"}, {"$set": {"availability": False}})
        preview = order_service.preview_order("s1")
    assert preview["unavailable_items"] == ["Coke"]
    assert {item["item_id"] for item in preview["items"]} == {"chicken-biryani"}


def test_preview_order_carries_special_instructions(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        cart_service.set_item_instructions("s1", "chicken-biryani", "extra spicy")
        preview = order_service.preview_order("s1")
    assert preview["items"][0]["special_instructions"] == "extra spicy"


# ---------------------------------------------------------------- create_order


def test_create_order_blocked_without_instructions_prompted(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        with pytest.raises(AppError) as exc_info:
            order_service.create_order("s1", "Somnath", "9876543210")
    assert exc_info.value.code == "instructions_not_prompted"


def test_create_order_blocked_on_empty_cart(seeded_menu):
    with seeded_menu.app_context():
        session_service.mark_instructions_prompted("s1")
        with pytest.raises(AppError) as exc_info:
            order_service.create_order("s1", "Somnath", "9876543210")
    assert exc_info.value.code == "cart_empty"


def test_create_order_blocked_on_invalid_customer_info(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        with pytest.raises(AppError) as exc_info:
            order_service.create_order("s1", "Somnath", "123")
    assert exc_info.value.code == "invalid_mobile"


def test_create_order_blocked_when_item_becomes_unavailable(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        db = get_db()
        db.menu.update_one({"item_id": "coke"}, {"$set": {"availability": False}})
        with pytest.raises(AppError) as exc_info:
            order_service.create_order("s1", "Somnath", "9876543210")
    assert exc_info.value.code == "items_unavailable"
    assert exc_info.value.status_code == 409
    assert "Coke" in exc_info.value.message


def test_create_order_reports_unavailable_even_when_it_was_the_only_item(seeded_menu):
    """Regression test: when EVERY cart line is unavailable, order_items ends up
    empty too -- must still report items_unavailable, not misreport cart_empty
    (a real bug caught by live-testing this exact scenario against the real
    Gemini API, which told the customer their cart was empty when it wasn't)."""
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        session_service.mark_instructions_prompted("s1")
        db = get_db()
        db.menu.update_one({"item_id": "chicken-biryani"}, {"$set": {"availability": False}})
        with pytest.raises(AppError) as exc_info:
            order_service.create_order("s1", "Somnath", "9876543210")
    assert exc_info.value.code == "items_unavailable"
    assert "Chicken Biryani" in exc_info.value.message


def test_create_order_succeeds_via_volunteered_instructions(seeded_menu):
    # No explicit mark_instructions_prompted call -- set_item_instructions
    # alone must satisfy the gate (PRD Section 27).
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        cart_service.set_item_instructions("s1", "chicken-biryani", "extra spicy")
        order = order_service.create_order("s1", "Somnath", "9876543210")
    assert order["status"] == "PENDING"
    assert order["items"][0]["special_instructions"] == "extra spicy"


def test_create_order_uses_live_price_not_stale_cart_price(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        db = get_db()
        db.menu.update_one({"item_id": "coke"}, {"$set": {"price": 90}})
        order = order_service.create_order("s1", "Somnath", "9876543210")
    coke_line = next(item for item in order["items"] if item["item_id"] == "coke")
    assert coke_line["price"] == 90
    assert coke_line["total"] == 180
    assert order["subtotal"] == 280 + 180


def test_create_order_full_shape_and_side_effects(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        cart_service.set_order_notes("s1", "No onion or garlic in anything")
        order = order_service.create_order("s1", "  Somnath  ", "9876543210")

    assert order["order_id"].startswith("ORD-")
    assert order["customer_name"] == "Somnath"  # trimmed by validate_customer_name
    assert order["mobile"] == "9876543210"
    assert order["order_notes"] == "No onion or garlic in anything"
    assert order["subtotal"] == order["total"] == 400
    assert order["status"] == "PENDING"
    assert "_id" not in order

    with seeded_menu.app_context():
        # Cart is cleared after a successful order.
        assert cart_service.get_cart("s1")["items"] == []
        # Customer info persisted onto the session.
        session = session_service.get_or_create("s1")
        assert session["customer_name"] == "Somnath"
        assert session["mobile"] == "9876543210"
        # And actually landed in MongoDB with the same shape.
        db = get_db()
        stored = db.orders.find_one({"order_id": order["order_id"]})
        assert stored["status"] == "PENDING"
        assert stored["total"] == 400


def test_preview_order_api_endpoint(client, seeded_menu):
    _confirm_ready_cart(seeded_menu, session_id="s-preview")
    response = client.post("/api/orders/preview", json={"session_id": "s-preview"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["total"] == 400
    assert data["unavailable_items"] == []
    # Read-only -- previewing must never create an order.
    assert client.get("/api/orders/ORD-does-not-exist").status_code == 404


def test_preview_order_api_endpoint_requires_session_id(client):
    response = client.post("/api/orders/preview", json={})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_create_order_api_endpoint(client, seeded_menu):
    _confirm_ready_cart(seeded_menu, session_id="s-api")
    response = client.post(
        "/api/orders",
        json={"session_id": "s-api", "customer_name": "Somnath", "mobile": "9876543210"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "PENDING"
    assert data["order_id"].startswith("ORD-")


def test_create_order_api_endpoint_blocked_without_instructions_prompted(client, seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s-api-2", "coke", 1)
    response = client.post(
        "/api/orders",
        json={"session_id": "s-api-2", "customer_name": "Somnath", "mobile": "9876543210"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "instructions_not_prompted"


# ---------------------------------------------------------------- get_order_status


def test_get_order_status_returns_restricted_view(seeded_menu):
    _confirm_ready_cart(seeded_menu)
    with seeded_menu.app_context():
        order = order_service.create_order("s1", "Somnath", "9876543210")
        status = order_service.get_order_status(order["order_id"])

    assert status["order_id"] == order["order_id"]
    assert status["status"] == "PENDING"
    assert status["total"] == 400
    # Never leaks PII -- knowing an order_id must not reveal who placed it.
    assert "customer_name" not in status
    assert "mobile" not in status


def test_get_order_status_404_for_unknown_order(seeded_menu):
    with seeded_menu.app_context():
        with pytest.raises(AppError) as exc_info:
            order_service.get_order_status("ORD-99999999-0000")
    assert exc_info.value.code == "order_not_found"
    assert exc_info.value.status_code == 404


def test_get_order_status_api_endpoint(client, seeded_menu):
    _confirm_ready_cart(seeded_menu, session_id="s-status")
    with seeded_menu.app_context():
        order = order_service.create_order("s-status", "Somnath", "9876543210")

    response = client.get(f"/api/orders/{order['order_id']}")
    assert response.status_code == 200
    assert response.get_json()["status"] == "PENDING"

    missing_response = client.get("/api/orders/ORD-00000000-0000")
    assert missing_response.status_code == 404


# ---------------------------------------------------------------- order ID concurrency


def test_next_order_sequence_is_unique_under_concurrency(app):
    """PRD Section 35: order IDs must be unique even under concurrent requests.

    mongomock's atomicity guarantees are looser than real MongoDB's, but
    this still exercises the find_one_and_update($inc) logic under real
    thread contention -- see ARCHITECTURE.md Section 10 for the note that a
    real-MongoDB check is the authoritative version of this property.
    """
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        try:
            with app.app_context():
                seq = order_service.next_order_sequence("concurrencytest")
            with lock:
                results.append(seq)
        except Exception as exc:  # pragma: no cover - failure path only
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(results) == 25
    assert len(set(results)) == 25  # no duplicates
    assert sorted(results) == list(range(1, 26))  # every slot filled exactly once
