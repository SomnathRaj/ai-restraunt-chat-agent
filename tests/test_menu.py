import pytest

from app.models.db import get_db
from app.services import menu_service


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
                    "is_veg": False,
                    "tags": ["chicken", "spicy"],
                    "active": True,
                },
                {
                    "item_id": "veg-biryani",
                    "name": "Veg Biryani",
                    "description": "Spiced rice with vegetables",
                    "category": "Main Course",
                    "price": 220,
                    "availability": True,
                    "is_veg": True,
                    "tags": ["vegetarian"],
                    "active": True,
                },
                {
                    "item_id": "veg-burger",
                    "name": "Veg Burger",
                    "description": "Vegetable patty burger",
                    "category": "Burger",
                    "price": 150,
                    "availability": True,
                    "tags": ["vegetarian", "burger"],
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
                {
                    "item_id": "discontinued-special",
                    "name": "Old Special",
                    "description": "No longer on the menu",
                    "category": "Main Course",
                    "price": 999,
                    "availability": True,
                    "tags": [],
                    "active": False,
                },
            ]
        )
    return app


def test_get_available_menu_excludes_unavailable_and_inactive(client, seeded_menu):
    response = client.get("/api/menu")
    assert response.status_code == 200
    item_ids = {item["item_id"] for item in response.get_json()}
    assert item_ids == {"chicken-biryani", "veg-biryani", "veg-burger"}


def test_get_available_menu_hides_internal_fields(client, seeded_menu):
    response = client.get("/api/menu")
    for item in response.get_json():
        assert "_id" not in item
        assert "active" not in item


def test_search_menu_matches_name_and_excludes_unavailable(client, seeded_menu):
    response = client.get("/api/menu/search?q=chicken")
    assert response.status_code == 200
    item_ids = {item["item_id"] for item in response.get_json()}
    # chicken-burger is unavailable, so only chicken-biryani should match.
    assert item_ids == {"chicken-biryani"}


def test_search_menu_matches_tags(client, seeded_menu):
    response = client.get("/api/menu/search?q=vegetarian")
    item_ids = {item["item_id"] for item in response.get_json()}
    assert item_ids == {"veg-biryani", "veg-burger"}


def test_menu_items_expose_is_veg(client, seeded_menu):
    response = client.get("/api/menu")
    by_id = {item["item_id"]: item for item in response.get_json()}
    assert by_id["chicken-biryani"]["is_veg"] is False
    assert by_id["veg-biryani"]["is_veg"] is True


def test_get_menu_item_404_when_missing(client, seeded_menu):
    response = client.get("/api/menu/does-not-exist")
    assert response.status_code == 404
    assert response.get_json()["error"] == "item_not_found"


def test_get_menu_item_found_even_if_unavailable(client, seeded_menu):
    response = client.get("/api/menu/chicken-burger")
    assert response.status_code == 200
    assert response.get_json()["availability"] is False


def test_get_menu_item_404_when_inactive(client, seeded_menu):
    response = client.get("/api/menu/discontinued-special")
    assert response.status_code == 404


def test_check_item_availability(seeded_menu):
    with seeded_menu.app_context():
        assert menu_service.check_item_availability("chicken-biryani") is True
        assert menu_service.check_item_availability("chicken-burger") is False
        assert menu_service.check_item_availability("does-not-exist") is False


def test_alternatives_prefer_same_category(seeded_menu):
    with seeded_menu.app_context():
        alternatives = menu_service.get_alternatives("chicken-burger")
    item_ids = [item["item_id"] for item in alternatives]
    assert item_ids[0] == "veg-burger"  # same Burger category, only available alt
    assert "chicken-burger" not in item_ids  # never returns the item itself
    assert "discontinued-special" not in item_ids  # never returns inactive items
