import pytest

from app.models.db import get_db
from app.services import menu_service
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


# ---------------------------------------------------------------------------
# find_item / resolve_item -- the AI may name a dish instead of its item_id
# ---------------------------------------------------------------------------


def test_find_item_by_id_or_exact_name_ignoring_case_and_spaces(seeded_menu):
    with seeded_menu.app_context():
        assert menu_service.find_item("veg-biryani")["name"] == "Veg Biryani"
        assert menu_service.find_item("Veg Biryani")["item_id"] == "veg-biryani"
        assert menu_service.find_item("  veg BIRYANI ")["item_id"] == "veg-biryani"


def test_find_item_is_exact_never_fuzzy(seeded_menu):
    # A near-miss must not quietly pick a different dish.
    with seeded_menu.app_context():
        assert menu_service.find_item("Biryani") is None
        assert menu_service.find_item("Veg Biryani Special") is None
        assert menu_service.find_item("") is None


def test_find_item_ignores_inactive_items_but_not_unavailable_ones(seeded_menu):
    with seeded_menu.app_context():
        assert menu_service.find_item("Old Special") is None  # active: False
        assert menu_service.find_item("Chicken Burger")["availability"] is False


def test_find_item_refuses_to_guess_between_duplicate_names(seeded_menu):
    with seeded_menu.app_context():
        get_db().menu.insert_one(
            {"item_id": "veg-biryani-2", "name": "Veg Biryani", "category": "Main Course", "price": 230,
             "availability": True, "tags": [], "active": True}
        )
        assert menu_service.find_item("Veg Biryani") is None
        assert menu_service.find_item("veg-biryani-2")["price"] == 230  # the id still works


def test_resolve_item_not_found_suggests_close_matches(seeded_menu):
    with seeded_menu.app_context():
        with pytest.raises(AppError) as info:
            menu_service.resolve_item("Biryani")
    assert info.value.code == "item_not_found"
    assert "Chicken Biryani (item_id chicken-biryani)" in info.value.message
    assert "Veg Biryani (item_id veg-biryani)" in info.value.message


def test_resolve_item_not_found_without_matches_has_no_tool_jargon(seeded_menu):
    with seeded_menu.app_context():
        with pytest.raises(AppError) as info:
            menu_service.resolve_item("Sushi")
    assert info.value.message == "No menu item found matching 'Sushi'."


def test_check_item_availability_accepts_a_name(seeded_menu):
    with seeded_menu.app_context():
        assert menu_service.check_item_availability("Veg Burger") is True
        assert menu_service.check_item_availability("chicken burger") is False
        assert menu_service.check_item_availability("Sushi") is False  # still a predicate, never raises


def test_rest_menu_item_lookup_stays_id_only(client, seeded_menu):
    assert client.get("/api/menu/veg-biryani").status_code == 200
    assert client.get("/api/menu/Veg Biryani").status_code == 404


@pytest.fixture
def diet_menu(app):
    def item(item_id, name, category, price, is_veg, availability=True):
        return {"item_id": item_id, "name": name, "category": category, "price": price, "is_veg": is_veg,
                "availability": availability, "tags": [], "active": True}

    with app.app_context():
        get_db().menu.insert_many(
            [
                item("chicken-burger", "Chicken Burger", "Burger", 220, False, availability=False),
                item("veg-burger", "Veg Burger", "Burger", 150, True, availability=False),
                item("cheese-burger", "Cheese Burger", "Burger", 170, True),
                item("double-chicken-burger", "Double Chicken Burger", "Burger", 260, False),
                item("chicken-wrap", "Chicken Wrap", "Wrap", 190, False),
                item("veg-wrap", "Veg Wrap", "Wrap", 160, True),
            ]
        )
    return app


def test_alternatives_rank_the_same_veg_or_non_veg_type_first(diet_menu):
    with diet_menu.app_context():
        for_chicken = [a["item_id"] for a in menu_service.get_alternatives("chicken-burger")]
        for_veg = [a["item_id"] for a in menu_service.get_alternatives("veg-burger")]
    # A chicken wrap beats a veg burger for a chicken-burger request, even across categories...
    assert for_chicken[:2] == ["double-chicken-burger", "chicken-wrap"]
    # ...and a veg customer is never offered meat ahead of veg dishes.
    assert for_veg[:2] == ["cheese-burger", "veg-wrap"]


def test_alternatives_accept_the_exact_name(diet_menu):
    with diet_menu.app_context():
        by_name = menu_service.get_alternatives("Chicken Burger")
        by_id = menu_service.get_alternatives("chicken-burger")
    assert by_name == by_id
    assert "chicken-burger" not in [a["item_id"] for a in by_name]
