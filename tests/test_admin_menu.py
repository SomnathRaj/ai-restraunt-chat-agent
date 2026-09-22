import pytest
from werkzeug.security import generate_password_hash

from app.models.db import get_db
from app.services import menu_service, order_service, session_service
from app.utils.errors import AppError

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"


@pytest.fixture(autouse=True)
def seeded_categories(app):
    with app.app_context():
        db = get_db()
        db.categories.insert_many(
            [{"name": "Starter"}, {"name": "Main Course"}, {"name": "Burger"}, {"name": "Beverage"}, {"name": "Dessert"}]
        )
    return app


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
                    "item_id": "discontinued-special",
                    "name": "Old Special",
                    "description": "No longer on the menu",
                    "category": "Main Course",
                    "price": 999,
                    "availability": True,
                    "is_veg": True,
                    "tags": [],
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


def test_create_menu_item_generates_slug_item_id(app):
    with app.app_context():
        item = menu_service.create_menu_item(
            name="Paneer Tikka",
            description="Grilled cottage cheese",
            category="Starter",
            price="180",
            availability=True,
            is_veg=True,
            tags="starter, spicy",
        )
    assert item["item_id"] == "paneer-tikka"
    assert item["active"] is True
    assert item["tags"] == ["starter", "spicy"]


def test_create_menu_item_dedupes_slug_on_name_collision(app):
    with app.app_context():
        first = menu_service.create_menu_item(
            name="Coke", description="", category="Beverage", price=60, availability=True, is_veg=True, tags=""
        )
        second = menu_service.create_menu_item(
            name="Coke", description="", category="Beverage", price=60, availability=True, is_veg=True, tags=""
        )
    assert first["item_id"] == "coke"
    assert second["item_id"] == "coke-2"


def test_create_menu_item_missing_name_raises(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            menu_service.create_menu_item(
                name="", description="", category="Starter", price=100, availability=True, is_veg=True, tags=""
            )
    assert excinfo.value.code == "missing_name"


def test_create_menu_item_rejects_category_not_in_predefined_list(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            menu_service.create_menu_item(
                name="Mystery Dish",
                description="",
                category="Not A Real Category",
                price=100,
                availability=True,
                is_veg=True,
                tags="",
            )
    assert excinfo.value.code == "invalid_category"


def test_list_categories_returns_seeded_names_sorted(app):
    with app.app_context():
        categories = menu_service.list_categories()
    assert categories == ["Beverage", "Burger", "Dessert", "Main Course", "Starter"]


def test_create_menu_item_invalid_price_raises(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            menu_service.create_menu_item(
                name="Mystery Dish",
                description="",
                category="Starter",
                price="not-a-number",
                availability=True,
                is_veg=True,
                tags="",
            )
    assert excinfo.value.code == "invalid_price"


def test_create_menu_item_rejects_zero_or_negative_price(app):
    with app.app_context():
        with pytest.raises(AppError):
            menu_service.create_menu_item(
                name="Free Item", description="", category="Starter", price=0, availability=True, is_veg=True, tags=""
            )


def test_update_menu_item_changes_fields(app, seeded_menu):
    with app.app_context():
        updated = menu_service.update_menu_item(
            "chicken-biryani",
            name="Chicken Biryani",
            description="Now extra spicy",
            category="Main Course",
            price=300,
            availability=False,
            is_veg=False,
            tags="chicken, rice",
        )
    assert updated["price"] == 300
    assert updated["availability"] is False
    assert updated["description"] == "Now extra spicy"
    assert updated["item_id"] == "chicken-biryani"


def test_update_menu_item_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            menu_service.update_menu_item(
                "does-not-exist",
                name="X",
                description="",
                category="Starter",
                price=10,
                availability=True,
                is_veg=True,
                tags="",
            )
    assert excinfo.value.status_code == 404


def test_set_menu_item_active_toggles(app, seeded_menu):
    with app.app_context():
        menu_service.set_menu_item_active("chicken-biryani", False)
        item = menu_service.get_menu_item_for_admin("chicken-biryani")
    assert item["active"] is False


def test_set_menu_item_active_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            menu_service.set_menu_item_active("does-not-exist", False)
    assert excinfo.value.status_code == 404


def test_delete_menu_item_removes_it(app, seeded_menu):
    with app.app_context():
        menu_service.delete_menu_item("chicken-biryani")
        db = get_db()
        assert db.menu.find_one({"item_id": "chicken-biryani"}) is None


def test_delete_menu_item_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            menu_service.delete_menu_item("does-not-exist")
    assert excinfo.value.status_code == 404


def test_list_all_menu_items_includes_inactive(app, seeded_menu):
    with app.app_context():
        items = menu_service.list_all_menu_items()
    item_ids = {item["item_id"] for item in items}
    assert item_ids == {"chicken-biryani", "discontinued-special"}


def test_list_menu_items_page_includes_inactive_and_paginates(app, seeded_menu):
    with app.app_context():
        result = menu_service.list_menu_items_page(page=1, page_size=1)
    assert result["total_count"] == 2
    assert result["total_pages"] == 2
    assert len(result["items"]) == 1

    with app.app_context():
        page2 = menu_service.list_menu_items_page(page=2, page_size=1)
    assert len(page2["items"]) == 1
    assert {result["items"][0]["item_id"], page2["items"][0]["item_id"]} == {
        "chicken-biryani",
        "discontinued-special",
    }


def test_list_menu_items_page_search_matches_name_category_tags(app, seeded_menu):
    with app.app_context():
        by_name = menu_service.list_menu_items_page(query="chicken")
        by_category = menu_service.list_menu_items_page(query="main course")
        by_tag = menu_service.list_menu_items_page(query="spicy")
        no_match = menu_service.list_menu_items_page(query="nonexistent-xyz")

    assert {item["item_id"] for item in by_name["items"]} == {"chicken-biryani"}
    assert {item["item_id"] for item in by_category["items"]} == {"chicken-biryani", "discontinued-special"}
    assert {item["item_id"] for item in by_tag["items"]} == {"chicken-biryani"}
    assert no_match["items"] == []
    assert no_match["total_count"] == 0


def test_list_menu_items_page_invalid_page_number_falls_back_safely(app, seeded_menu):
    with app.app_context():
        too_high = menu_service.list_menu_items_page(page=999)
        too_low = menu_service.list_menu_items_page(page=0)
    assert len(too_high["items"]) == 2
    assert too_high["page"] == too_high["total_pages"]
    assert too_low["page"] == 1


def test_get_menu_item_for_admin_exposes_active_field(app, seeded_menu):
    with app.app_context():
        item = menu_service.get_menu_item_for_admin("discontinued-special")
    assert item["active"] is False


def test_deactivating_item_hides_from_customer_menu_but_leaves_past_order_intact(app, seeded_menu):
    with app.app_context():
        session_service.set_customer_info("s1", "Somnath", "9876543210")
        from app.services import cart_service

        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        session_service.mark_instructions_prompted("s1")
        order = order_service.create_order("s1", "Somnath", "9876543210")

        menu_service.set_menu_item_active("chicken-biryani", False)

        available_ids = {item["item_id"] for item in menu_service.get_available_menu()}
        assert "chicken-biryani" not in available_ids

        stored_order = order_service.get_order_status(order["order_id"])
        assert stored_order["items"][0]["item_id"] == "chicken-biryani"
        assert stored_order["total"] == order["total"]


# ---------------------------------------------------------------------------
# Admin HTTP route tests
# ---------------------------------------------------------------------------


def test_menu_list_requires_login(client):
    response = client.get("/admin/menu")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_menu_list_search_via_query_param(admin_client, seeded_menu):
    response = admin_client.get("/admin/menu?q=Old Special")
    assert response.status_code == 200
    assert b"Old Special" in response.data
    assert b"Chicken Biryani" not in response.data


def test_menu_list_shows_no_results_message_for_unmatched_search(admin_client, seeded_menu):
    response = admin_client.get("/admin/menu?q=nonexistent-xyz")
    assert response.status_code == 200
    assert b"No menu items match your search." in response.data


def test_menu_list_pagination_via_query_param(admin_client, seeded_menu):
    response = admin_client.get("/admin/menu?page=1")
    assert response.status_code == 200
    assert b"2 total" in response.data


def test_admin_can_create_menu_item_via_form(admin_client, app):
    response = admin_client.post(
        "/admin/menu/new",
        data={
            "name": "Gulab Jamun",
            "description": "Sweet dessert",
            "category": "Dessert",
            "price": "90",
            "tags": "dessert, sweet",
            "is_veg": "on",
            "availability": "on",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        item = db.menu.find_one({"item_id": "gulab-jamun"})
    assert item is not None
    assert item["price"] == 90.0
    assert item["is_veg"] is True


def test_admin_menu_form_shows_error_on_invalid_price_never_500(admin_client):
    response = admin_client.post(
        "/admin/menu/new",
        data={"name": "Broken Item", "description": "", "category": "Starter", "price": "abc", "tags": ""},
    )
    assert response.status_code == 400
    assert b"Price must be a number" in response.data


def test_admin_menu_form_shows_error_on_missing_name_never_500(admin_client):
    response = admin_client.post(
        "/admin/menu/new",
        data={"name": "", "description": "", "category": "Starter", "price": "100", "tags": ""},
    )
    assert response.status_code == 400
    assert b"Name is required" in response.data


def test_menu_list_renders_active_toggle_as_switch_not_button(admin_client, seeded_menu):
    response = admin_client.get("/admin/menu")
    assert response.status_code == 200
    html = response.data.decode()
    assert 'class="admin-switch"' in html
    assert ">Deactivate<" not in html
    assert ">Activate<" not in html

    # chicken-biryani is active -> its toggle is checked; discontinued-special
    # is inactive -> its toggle is not.
    active_toggle = html.split('action="/admin/menu/chicken-biryani/toggle-active"')[1].split("</form>")[0]
    inactive_toggle = html.split('action="/admin/menu/discontinued-special/toggle-active"')[1].split("</form>")[0]
    assert "checked" in active_toggle
    assert "checked" not in inactive_toggle


def test_menu_list_edit_and_delete_are_icon_buttons_without_text_labels(admin_client, seeded_menu):
    response = admin_client.get("/admin/menu")
    assert response.status_code == 200
    assert b'class="admin-icon-button"' in response.data
    assert b'class="admin-icon-button admin-icon-button-danger"' in response.data
    assert b">Edit<" not in response.data
    assert b">Delete<" not in response.data
    # Accessible names must still be present even without visible text.
    assert b'aria-label="Edit Chicken Biryani"' in response.data
    assert b'aria-label="Delete Chicken Biryani"' in response.data


def test_menu_list_delete_confirm_message_is_not_interpolated_into_inline_js(admin_client, app):
    """An item name containing a quote must never land inside an inline
    event-handler JS string (onsubmit="...confirm('...NAME...')...") --
    HTML-attribute escaping alone doesn't protect that context, since browsers
    HTML-decode the attribute back to raw characters before parsing it as JS,
    letting a crafted name break out of the string literal and execute
    arbitrary JS in another admin's session. The name must only ever appear
    in a plain data-* attribute, read by an external script, never spliced
    directly into a JS string in the template."""
    admin_client.post(
        "/admin/menu/new",
        data={
            "name": "Naan'); alert(document.cookie); //",
            "description": "",
            "category": "Starter",
            "price": "50",
            "tags": "",
        },
    )
    response = admin_client.get("/admin/menu")
    assert response.status_code == 200
    body = response.data.decode()

    assert "onsubmit=\"return confirm('Permanently delete" not in body
    assert 'class="admin-delete-form"' in body
    assert "form.dataset.confirmMessage" in body
    # The name is present only inside the data-* attribute (HTML-escaped by
    # Jinja: ' becomes &#39;), never spliced into a JS string literal.
    assert 'data-confirm-message="Permanently delete Naan&#39;); alert(document.cookie); //?' in body


def test_admin_menu_new_form_renders_category_dropdown(admin_client):
    response = admin_client.get("/admin/menu/new")
    assert response.status_code == 200
    assert b'<select id="category" name="category"' in response.data
    assert b">Starter</option>" in response.data
    assert b">Beverage</option>" in response.data


def test_admin_menu_new_form_availability_checkbox_checked_by_default(admin_client):
    response = admin_client.get("/admin/menu/new")
    assert response.status_code == 200
    html = response.data.decode()
    checkbox = html.split('name="availability"')[1].split("/>")[0]
    assert "checked" in checkbox


def test_admin_menu_edit_form_availability_checkbox_reflects_stored_value(app, admin_client):
    with app.app_context():
        db = get_db()
        db.menu.insert_many(
            [
                {
                    "item_id": "out-of-stock-item",
                    "name": "Out Of Stock Item",
                    "description": "",
                    "category": "Starter",
                    "price": 100,
                    "availability": False,
                    "is_veg": True,
                    "tags": [],
                    "active": True,
                },
                {
                    "item_id": "in-stock-item",
                    "name": "In Stock Item",
                    "description": "",
                    "category": "Starter",
                    "price": 100,
                    "availability": True,
                    "is_veg": True,
                    "tags": [],
                    "active": True,
                },
            ]
        )

    unavailable_response = admin_client.get("/admin/menu/out-of-stock-item/edit")
    html = unavailable_response.data.decode()
    checkbox = html.split('name="availability"')[1].split("/>")[0]
    assert "checked" not in checkbox

    available_response = admin_client.get("/admin/menu/in-stock-item/edit")
    html2 = available_response.data.decode()
    checkbox2 = html2.split('name="availability"')[1].split("/>")[0]
    assert "checked" in checkbox2


def test_admin_menu_form_rejects_category_not_in_predefined_list(admin_client):
    response = admin_client.post(
        "/admin/menu/new",
        data={"name": "Mystery Dish", "description": "", "category": "Not A Real Category", "price": "100", "tags": ""},
    )
    assert response.status_code == 400
    assert b"is not a valid category" in response.data


def test_admin_can_edit_menu_item_via_form(admin_client, app, seeded_menu):
    response = admin_client.post(
        "/admin/menu/chicken-biryani/edit",
        data={
            "name": "Chicken Biryani",
            "description": "Updated description",
            "category": "Main Course",
            "price": "310",
            "tags": "chicken, rice",
            "availability": "on",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        item = menu_service.get_menu_item_for_admin("chicken-biryani")
    assert item["price"] == 310.0
    assert item["description"] == "Updated description"


def test_admin_can_toggle_active_via_button(admin_client, app, seeded_menu):
    response = admin_client.post("/admin/menu/chicken-biryani/toggle-active")
    assert response.status_code == 302
    with app.app_context():
        item = menu_service.get_menu_item_for_admin("chicken-biryani")
    assert item["active"] is False


def test_admin_can_delete_menu_item_via_button(admin_client, app, seeded_menu):
    response = admin_client.post("/admin/menu/chicken-biryani/delete")
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        assert db.menu.find_one({"item_id": "chicken-biryani"}) is None
