import pytest

from app.models.db import get_db
from app.services import cart_service
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
                    "tags": ["chicken", "spicy"],
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
                {
                    "item_id": "chicken-burger",
                    "name": "Chicken Burger",
                    "description": "Grilled chicken patty burger",
                    "category": "Burger",
                    "price": 220,
                    "availability": False,
                    "tags": ["chicken", "burger"],
                    "active": True,
                },
            ]
        )
    return app


def test_add_to_cart_uses_price_from_mongo(seeded_menu):
    with seeded_menu.app_context():
        cart = cart_service.add_to_cart("s1", "chicken-biryani", 1)
    line = cart["items"][0]
    assert line["price"] == 280
    assert cart["subtotal"] == 280
    assert cart["total"] == 280


def test_add_to_cart_merges_quantity_for_same_item(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart = cart_service.add_to_cart("s1", "coke", 2)
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 3


def test_add_to_cart_rejects_unavailable_item(seeded_menu):
    from app.utils.errors import AppError

    with seeded_menu.app_context():
        with pytest.raises(AppError) as exc_info:
            cart_service.add_to_cart("s1", "chicken-burger", 1)
    assert exc_info.value.code == "item_unavailable"


def test_add_to_cart_rejects_missing_item(seeded_menu):
    from app.utils.errors import AppError

    with seeded_menu.app_context():
        with pytest.raises(AppError) as exc_info:
            cart_service.add_to_cart("s1", "does-not-exist", 1)
    assert exc_info.value.code == "item_not_found"


@pytest.mark.parametrize("bad_quantity", [0, -1, "not-a-number", 999])
def test_add_to_cart_rejects_invalid_quantity(seeded_menu, bad_quantity):
    from app.utils.errors import AppError

    with seeded_menu.app_context():
        with pytest.raises(AppError) as exc_info:
            cart_service.add_to_cart("s1", "coke", bad_quantity)
    assert exc_info.value.code == "invalid_quantity"


def test_update_cart_quantity_to_zero_removes_the_line(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 2)
        cart = cart_service.update_cart_quantity("s1", "coke", 0)
    assert cart["items"] == []


def test_update_cart_quantity_changes_quantity(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart = cart_service.update_cart_quantity("s1", "coke", 5)
    assert cart["items"][0]["quantity"] == 5


def test_update_cart_quantity_404_when_item_not_in_cart(seeded_menu):
    from app.utils.errors import AppError

    with seeded_menu.app_context():
        with pytest.raises(AppError) as exc_info:
            cart_service.update_cart_quantity("s1", "coke", 2)
    assert exc_info.value.code == "item_not_in_cart"


def test_remove_from_cart(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        cart = cart_service.remove_from_cart("s1", "coke")
    item_ids = {line["item_id"] for line in cart["items"]}
    assert item_ids == {"chicken-biryani"}


def test_clear_cart(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart = cart_service.clear_cart("s1")
    assert cart == {"items": [], "order_notes": None, "subtotal": 0, "total": 0}


def test_set_item_instructions_never_changes_price_or_quantity(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        # Even a request phrased like a menu/quantity change must not alter
        # price/quantity -- set_item_instructions only ever sets a string.
        cart = cart_service.set_item_instructions("s1", "chicken-biryani", "extra chicken please")
    line = cart["items"][0]
    assert line["instructions"] == "extra chicken please"
    assert line["price"] == 280
    assert line["quantity"] == 1
    assert cart["total"] == 280  # unchanged by the instruction text


def test_set_item_instructions_404_when_item_not_in_cart(seeded_menu):
    from app.utils.errors import AppError

    with seeded_menu.app_context():
        with pytest.raises(AppError) as exc_info:
            cart_service.set_item_instructions("s1", "coke", "no ice")
    assert exc_info.value.code == "item_not_in_cart"


def test_set_order_notes_never_changes_total(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 2)
        before = cart_service.get_cart("s1")
        cart = cart_service.set_order_notes("s1", "No onion or garlic in anything")
    assert cart["order_notes"] == "No onion or garlic in anything"
    assert cart["total"] == before["total"]


def test_instructions_are_sanitized_and_length_capped(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart = cart_service.set_item_instructions("s1", "coke", "no ice\x00" + "x" * 300)
    assert "\x00" not in cart["items"][0]["instructions"]
    assert len(cart["items"][0]["instructions"]) <= 200


def test_calculate_order_total_matches_get_cart(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        cart_service.add_to_cart("s1", "coke", 2)
        totals = cart_service.calculate_order_total("s1")
        cart = cart_service.get_cart("s1")
    assert totals["subtotal"] == cart["subtotal"] == 400
    assert totals["total"] == cart["total"] == 400


def test_add_to_cart_api_endpoint(client, seeded_menu):
    response = client.post("/api/cart", json={"session_id": "s2", "action": "add", "item_id": "coke", "quantity": 2})
    assert response.status_code == 200
    data = response.get_json()
    assert data["items"][0]["item_id"] == "coke"
    assert data["items"][0]["quantity"] == 2

    get_response = client.get("/api/cart/s2")
    assert get_response.get_json()["total"] == 120


def test_carts_are_isolated_per_session(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("session-a", "coke", 1)
        cart_b = cart_service.get_cart("session-b")
    assert cart_b["items"] == []


# ---------------------------------------------------------------------------
# Items may be referred to by exact menu name (fewer AI round-trips)
# ---------------------------------------------------------------------------


def test_add_to_cart_by_name_stores_the_real_item_id_and_database_price(seeded_menu):
    with seeded_menu.app_context():
        cart = cart_service.add_to_cart("s1", "chicken biryani", 2)
    assert cart["items"] == [
        {"item_id": "chicken-biryani", "name": "Chicken Biryani", "price": 280, "quantity": 2, "instructions": None}
    ]
    assert cart["total"] == 560


def test_add_to_cart_by_name_merges_with_the_same_item_added_by_id(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart = cart_service.add_to_cart("s1", "Coke", 2)
    assert [(line["item_id"], line["quantity"]) for line in cart["items"]] == [("coke", 3)]


def test_add_to_cart_by_name_still_rejects_unavailable_items(seeded_menu):
    with seeded_menu.app_context():
        with pytest.raises(AppError) as info:
            cart_service.add_to_cart("s1", "Chicken Burger", 1)
    assert info.value.code == "item_unavailable"


def test_cart_line_operations_accept_the_name(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        cart_service.set_item_instructions("s1", "Chicken Biryani", "extra spicy")
        cart_service.update_cart_quantity("s1", "COKE", 3)
        cart = cart_service.remove_from_cart("s1", "chicken biryani")
    assert cart["items"] == [{"item_id": "coke", "name": "Coke", "price": 60, "quantity": 3, "instructions": None}]


def test_remove_from_cart_by_unknown_name_is_still_a_no_op(seeded_menu):
    with seeded_menu.app_context():
        cart_service.add_to_cart("s1", "coke", 1)
        cart = cart_service.remove_from_cart("s1", "Pepsi")
    assert len(cart["items"]) == 1
