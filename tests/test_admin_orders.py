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
                    "is_veg": True,
                    "tags": ["drink"],
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


def _place_customer_order(app, session_id="s1"):
    with app.app_context():
        cart_service.add_to_cart(session_id, "chicken-biryani", 1)
        session_service.mark_instructions_prompted(session_id)
        return order_service.create_order(session_id, "Somnath", "9876543210")


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


def test_create_order_defaults_payment_status_and_created_by(app, seeded_menu):
    order = _place_customer_order(app)
    assert order["payment_status"] == "DUE"
    assert order["created_by"] == "customer"


def test_list_orders_page_most_recent_first(app, seeded_menu):
    with app.app_context():
        order1 = order_service.create_order_by_admin(
            [{"item_id": "coke", "quantity": 1}], "Customer One", "9876543210"
        )
        order2 = order_service.create_order_by_admin(
            [{"item_id": "coke", "quantity": 1}], "Customer Two", "9876543211"
        )
        result = order_service.list_orders_page()
    assert [o["order_id"] for o in result["orders"][:2]] == [order2["order_id"], order1["order_id"]]
    assert result["total_count"] == 2


def test_list_orders_page_pagination(app, seeded_menu):
    with app.app_context():
        ids = [
            order_service.create_order_by_admin([{"item_id": "coke", "quantity": 1}], "C", "9876543210")["order_id"]
            for _ in range(3)
        ]
        first_page = order_service.list_orders_page(page=1, page_size=2)
        second_page = order_service.list_orders_page(page=2, page_size=2)
    assert [o["order_id"] for o in first_page["orders"]] == [ids[2], ids[1]]
    assert first_page["total_pages"] == 2
    assert [o["order_id"] for o in second_page["orders"]] == [ids[0]]


def test_list_orders_page_search_by_customer_name(app, seeded_menu):
    with app.app_context():
        order_service.create_order_by_admin([{"item_id": "coke", "quantity": 1}], "Alice Smith", "9876543210")
        order_service.create_order_by_admin([{"item_id": "coke", "quantity": 1}], "Bob Jones", "9876543211")
        result = order_service.list_orders_page(query="alice")
    assert result["total_count"] == 1
    assert result["orders"][0]["customer_name"] == "Alice Smith"


def test_list_orders_page_search_by_item_name(app, seeded_menu):
    with app.app_context():
        order_service.create_order_by_admin([{"item_id": "coke", "quantity": 1}], "Alice", "9876543210")
        order_service.create_order_by_admin(
            [{"item_id": "chicken-biryani", "quantity": 1}], "Bob", "9876543211"
        )
        result = order_service.list_orders_page(query="biryani")
    assert result["total_count"] == 1
    assert result["orders"][0]["customer_name"] == "Bob"


def test_list_orders_page_search_no_match_returns_empty(app, seeded_menu):
    with app.app_context():
        order_service.create_order_by_admin([{"item_id": "coke", "quantity": 1}], "Alice", "9876543210")
        result = order_service.list_orders_page(query="no-such-order")
    assert result["orders"] == []
    assert result["total_count"] == 0


def test_get_order_for_admin_exposes_pii_unlike_get_order_status(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        full = order_service.get_order_for_admin(order["order_id"])
    assert full["customer_name"] == "Somnath"
    assert full["mobile"] == "9876543210"
    assert full["payment_status"] == "DUE"


def test_get_order_for_admin_missing_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            order_service.get_order_for_admin("ORD-does-not-exist")
    assert excinfo.value.status_code == 404


def test_update_order_status_valid_transition(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        updated = order_service.update_order_status(order["order_id"], "PROCESSING_FOOD")
    assert updated["status"] == "PROCESSING_FOOD"


def test_mark_kt_generated_advances_pending_to_processing_food(app, seeded_menu):
    order = _place_customer_order(app)
    assert order["status"] == "PENDING"
    with app.app_context():
        updated = order_service.mark_kt_generated(order["order_id"])
    assert updated["status"] == "PROCESSING_FOOD"
    with app.app_context():
        assert order_service.get_order_status(order["order_id"])["status"] == "PROCESSING_FOOD"


def test_mark_kt_generated_does_not_touch_other_statuses(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        order_service.update_order_status(order["order_id"], "COMPLETED")
        # Reprinting a KT for an already-completed order must not revert it.
        result = order_service.mark_kt_generated(order["order_id"])
    assert result["status"] == "COMPLETED"

    order2 = _place_customer_order(app, session_id="s2")
    with app.app_context():
        order_service.update_order_status(order2["order_id"], "CANCEL")
        result2 = order_service.mark_kt_generated(order2["order_id"])
    assert result2["status"] == "CANCEL"


def test_mark_kt_generated_is_idempotent_on_repeat_calls(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        first = order_service.mark_kt_generated(order["order_id"])
        second = order_service.mark_kt_generated(order["order_id"])
    assert first["status"] == "PROCESSING_FOOD"
    assert second["status"] == "PROCESSING_FOOD"


def test_mark_kt_generated_missing_order_raises_404(app):
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            order_service.mark_kt_generated("ORD-does-not-exist")
    assert excinfo.value.status_code == 404


def test_update_order_status_allows_cancel(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        updated = order_service.update_order_status(order["order_id"], "CANCEL")
    assert updated["status"] == "CANCEL"


def test_update_order_status_rejects_invalid_value(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            order_service.update_order_status(order["order_id"], "CANCELLED")
    assert excinfo.value.code == "invalid_status"
    with app.app_context():
        # Rejected write must not have touched the stored status.
        assert order_service.get_order_status(order["order_id"])["status"] == "PENDING"


def test_update_payment_status_valid_transition(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        updated = order_service.update_payment_status(order["order_id"], "PAID")
    assert updated["payment_status"] == "PAID"


def test_update_payment_status_rejects_invalid_value(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            order_service.update_payment_status(order["order_id"], "REFUNDED")
    assert excinfo.value.code == "invalid_payment_status"


def test_update_order_items_recalculates_totals_from_fresh_prices(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        db = get_db()
        db.menu.update_one({"item_id": "chicken-biryani"}, {"$set": {"price": 350}})
        updated = order_service.update_order_items(
            order["order_id"], [{"item_id": "chicken-biryani", "quantity": 2}, {"item_id": "coke", "quantity": 1}]
        )
    assert updated["subtotal"] == updated["total"] == 350 * 2 + 60
    assert len(updated["items"]) == 2


def test_update_order_items_rejects_empty_list(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            order_service.update_order_items(order["order_id"], [])
    assert excinfo.value.code == "order_empty"


def test_update_order_items_rejects_invalid_quantity(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        with pytest.raises(AppError) as excinfo:
            order_service.update_order_items(order["order_id"], [{"item_id": "coke", "quantity": 0}])
    assert excinfo.value.code == "invalid_quantity"


def test_update_order_items_rejects_unknown_item(app, seeded_menu):
    order = _place_customer_order(app)
    with app.app_context():
        with pytest.raises(AppError):
            order_service.update_order_items(order["order_id"], [{"item_id": "does-not-exist", "quantity": 1}])


def test_create_order_by_admin_starts_processing_food_and_due(app, seeded_menu):
    with app.app_context():
        order = order_service.create_order_by_admin(
            [{"item_id": "chicken-biryani", "quantity": 2, "special_instructions": "extra spicy"}],
            "Phone Customer",
            "9876543212",
        )
    assert order["status"] == "PROCESSING_FOOD"
    assert order["payment_status"] == "DUE"
    assert order["created_by"] == "admin"
    assert order["order_id"].startswith("ORD-")
    assert order["total"] == 560
    assert order["items"][0]["special_instructions"] == "extra spicy"


def test_create_order_by_admin_rejects_invalid_mobile(app, seeded_menu):
    with app.app_context():
        with pytest.raises(AppError):
            order_service.create_order_by_admin([{"item_id": "coke", "quantity": 1}], "Someone", "123")


# ---------------------------------------------------------------------------
# Admin HTTP route tests
# ---------------------------------------------------------------------------


def test_orders_list_requires_login(client):
    response = client.get("/admin/orders")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_orders_list_search_via_query_param(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders?q={order['customer_name']}")
    assert response.status_code == 200
    assert order["order_id"].encode() in response.data


def test_orders_list_shows_no_results_message_for_unmatched_search(admin_client, app, seeded_menu):
    _place_customer_order(app)
    response = admin_client.get("/admin/orders?q=nonexistent-xyz")
    assert response.status_code == 200
    assert b"No orders match your search." in response.data


def test_admin_can_view_order_detail(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders/{order['order_id']}")
    assert response.status_code == 200
    assert b"Somnath" in response.data
    assert order["order_id"].encode() in response.data


def test_order_detail_add_item_dropdown_is_searchable(admin_client, app, seeded_menu):
    """The 'Add item' <select> is progressively enhanced into a searchable
    dropdown (Tom Select) -- the underlying <select id=add_item_id
    name=add_item_id> must still be present unchanged so form submission
    keeps working with zero backend changes."""
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders/{order['order_id']}")
    assert response.status_code == 200
    body = response.data.decode()
    assert 'id="add_item_id" name="add_item_id"' in body
    assert "tom-select" in body
    assert "new TomSelect(\"#add_item_id\"" in body


def test_order_detail_add_item_dropdown_shows_veg_nonveg_dot(admin_client, app, seeded_menu):
    """Each addable <option> carries its veg/non-veg flag as a data-data JSON
    attribute, and TomSelect is configured with dataAttr: "data" (the
    dataset-key form, NOT the literal attribute name "data-data" -- Tom
    Select reads it via element.dataset[dataAttr], and the DOM camel-cases
    data-data to dataset.data) so the dropdown's render.option/render.item
    callbacks can actually see data.is_veg instead of always getting
    undefined."""
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders/{order['order_id']}")
    assert response.status_code == 200
    body = response.data.decode()
    assert 'data-data=\'{"is_veg":true}\'' in body  # coke, the only addable item (chicken-biryani is on the order already)
    assert 'dataAttr: "data"' in body
    assert "renderItemWithDietDot" in body
    assert "diet-dot" in body


def test_order_detail_price_summary_is_below_order_id_and_order_id_is_copyable(admin_client, app, seeded_menu):
    """Subtotal/Total moved up to sit directly under the Order ID heading
    (matching its font via the shared .admin-order-price-summary class), and
    the order ID itself has a copy-to-clipboard button next to it."""
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders/{order['order_id']}")
    assert response.status_code == 200
    body = response.data.decode()

    header_pos = body.index("admin-page-header")
    price_pos = body.index("admin-order-price-summary")
    meta_pos = body.index("admin-order-meta")
    assert header_pos < price_pos < meta_pos

    assert f'<span id="order-id-value">{order["order_id"]}</span>' in body
    assert 'id="copy-order-id"' in body
    assert "navigator.clipboard.writeText" in body


def test_order_detail_has_generate_kt_button_before_back_to_orders(admin_client, app, seeded_menu):
    """The order detail page's header row gets its own Generate KT button --
    same POST target/target=_blank-new-tab behavior as the Orders list row
    button -- positioned before "Back to orders"."""
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders/{order['order_id']}")
    assert response.status_code == 200
    body = response.data.decode()

    kt_pos = body.index(f'action="/admin/orders/{order["order_id"]}/generate-kt"')
    back_pos = body.index("Back to orders")
    assert kt_pos < back_pos
    assert 'target="_blank"' in body
    assert ">Generate KT<" in body


def test_order_detail_has_invoice_button_before_generate_kt(admin_client, app, seeded_menu):
    """Same header row also gets a Print Invoice link (plain GET, no CSRF
    token needed) positioned before Generate KT."""
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders/{order['order_id']}")
    assert response.status_code == 200
    body = response.data.decode()

    invoice_pos = body.index(f'href="/admin/orders/{order["order_id"]}/invoice"')
    kt_pos = body.index(f'action="/admin/orders/{order["order_id"]}/generate-kt"')
    assert invoice_pos < kt_pos
    assert ">Print Invoice<" in body
    assert 'class="admin-button admin-button-success"' in body


def test_admin_can_update_order_status_via_form(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(f"/admin/orders/{order['order_id']}/status", data={"status": "COMPLETED"})
    assert response.status_code == 302
    with app.app_context():
        assert order_service.get_order_status(order["order_id"])["status"] == "COMPLETED"


def test_generate_kt_advances_status_and_redirects_to_print_view(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(f"/admin/orders/{order['order_id']}/generate-kt")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/admin/orders/{order['order_id']}/kt"
    with app.app_context():
        assert order_service.get_order_status(order["order_id"])["status"] == "PROCESSING_FOOD"


def test_generate_kt_on_non_pending_order_leaves_status_unchanged(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    admin_client.post(f"/admin/orders/{order['order_id']}/status", data={"status": "COMPLETED"})

    response = admin_client.post(f"/admin/orders/{order['order_id']}/generate-kt")
    assert response.status_code == 302
    with app.app_context():
        assert order_service.get_order_status(order["order_id"])["status"] == "COMPLETED"


def test_kt_print_view_shows_items_quantities_instructions_and_notes(admin_client, app, seeded_menu):
    with app.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 2)
        cart_service.set_item_instructions("s1", "chicken-biryani", "extra spicy")
        cart_service.set_order_notes("s1", "ring the bell twice")
        order = order_service.create_order("s1", "Somnath", "9876543210")

    response = admin_client.get(f"/admin/orders/{order['order_id']}/kt")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Chicken Biryani" in body
    assert "x2" not in body  # quantity shown as a plain number, no "x" prefix
    assert "<span>2</span>" in body
    assert "<span>Item</span>" in body
    assert "<span>QTY</span>" in body
    assert "extra spicy" in body
    assert "ring the bell twice" in body
    assert order["order_id"] in body
    # A KT is for the kitchen -- it must never show prices/totals.
    assert "₹" not in body


def test_kt_print_view_requires_login(client, app, seeded_menu):
    order = _place_customer_order(app)
    response = client.get(f"/admin/orders/{order['order_id']}/kt")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_generate_kt_requires_login(client, app, seeded_menu):
    order = _place_customer_order(app)
    response = client.post(f"/admin/orders/{order['order_id']}/generate-kt")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"
    with app.app_context():
        # Must not have mutated status while unauthenticated.
        assert order_service.get_order_status(order["order_id"])["status"] == "PENDING"


def test_orders_list_shows_generate_kt_button(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.get("/admin/orders")
    assert response.status_code == 200
    assert f'action="/admin/orders/{order["order_id"]}/generate-kt"'.encode() in response.data
    assert b'aria-label="Generate Kitchen Token' in response.data


def test_orders_list_shows_invoice_button_for_both_due_and_paid_orders(admin_client, app, seeded_menu):
    due_order = _place_customer_order(app, session_id="s1")
    paid_order = _place_customer_order(app, session_id="s2")
    admin_client.post(f"/admin/orders/{paid_order['order_id']}/payment-status", data={"payment_status": "PAID"})

    response = admin_client.get("/admin/orders")
    assert response.status_code == 200
    body = response.data.decode()

    assert f'href="/admin/orders/{paid_order["order_id"]}/invoice"' in body
    assert f'aria-label="Print Invoice for order {paid_order["order_id"]}"' in body
    assert f'href="/admin/orders/{due_order["order_id"]}/invoice"' in body
    assert f'aria-label="Print Invoice for order {due_order["order_id"]}"' in body
    assert 'class="admin-icon-button admin-icon-button-success"' in body


def test_invoice_print_view_shows_order_id_customer_mobile_items_and_total(admin_client, app, seeded_menu):
    with app.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 2)
        cart_service.add_to_cart("s1", "coke", 1)
        session_service.mark_instructions_prompted("s1")
        order = order_service.create_order("s1", "Somnath", "9876543210")

    response = admin_client.get(f"/admin/orders/{order['order_id']}/invoice")
    assert response.status_code == 200
    body = response.data.decode()
    assert order["order_id"] in body
    assert "Somnath" in body
    assert "9876543210" in body
    assert "Chicken Biryani" in body
    assert "Coke" in body
    assert f"₹{order['total']:.2f}" in body
    assert order["total"] == 620
    assert "In words: Rupees Six Hundred Twenty Only" in body
    assert "Thank you for dining with us!" in body
    # No UPI_ID configured in TestConfig -- the QR block must be skipped
    # entirely, not rendered with a placeholder/broken image.
    assert "data:image/png;base64," not in body
    assert "Scan to pay via UPI" not in body


def test_invoice_print_view_requires_login(client, app, seeded_menu):
    order = _place_customer_order(app)
    response = client.get(f"/admin/orders/{order['order_id']}/invoice")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_admin_can_cancel_order_via_form(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(f"/admin/orders/{order['order_id']}/status", data={"status": "CANCEL"})
    assert response.status_code == 302
    with app.app_context():
        assert order_service.get_order_status(order["order_id"])["status"] == "CANCEL"


def test_admin_order_status_form_rejects_invalid_value_never_500(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(f"/admin/orders/{order['order_id']}/status", data={"status": "BOGUS"})
    assert response.status_code == 400
    assert b"Status must be one of" in response.data


def test_admin_can_update_payment_status_via_form(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(f"/admin/orders/{order['order_id']}/payment-status", data={"payment_status": "PAID"})
    assert response.status_code == 302
    with app.app_context():
        assert order_service.get_order_for_admin(order["order_id"])["payment_status"] == "PAID"


def test_admin_can_update_existing_order_item_quantity_via_form(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(
        f"/admin/orders/{order['order_id']}/items",
        data={"qty__chicken-biryani": "2", "note__chicken-biryani": "no onion"},
    )
    assert response.status_code == 302
    with app.app_context():
        updated = order_service.get_order_for_admin(order["order_id"])
    assert updated["total"] == 280 * 2
    assert updated["items"][0]["special_instructions"] == "no onion"


def test_admin_can_add_a_new_item_to_an_existing_order_via_form(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(
        f"/admin/orders/{order['order_id']}/items",
        data={
            "qty__chicken-biryani": "1",
            "add_item_id": "coke",
            "add_item_qty": "1",
            "add_item_note": "extra cold",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        updated = order_service.get_order_for_admin(order["order_id"])
    assert updated["total"] == 280 + 60
    item_ids = {item["item_id"] for item in updated["items"]}
    assert item_ids == {"chicken-biryani", "coke"}
    coke_line = next(item for item in updated["items"] if item["item_id"] == "coke")
    assert coke_line["special_instructions"] == "extra cold"


def test_admin_can_edit_order_notes_via_form(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(
        f"/admin/orders/{order['order_id']}/items",
        data={"qty__chicken-biryani": "1", "order_notes": "no onion, extra sauce"},
    )
    assert response.status_code == 302
    with app.app_context():
        updated = order_service.get_order_for_admin(order["order_id"])
    assert updated["order_notes"] == "no onion, extra sauce"


def test_order_detail_only_lists_items_already_in_the_order(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.get(f"/admin/orders/{order['order_id']}")
    assert response.status_code == 200
    # chicken-biryani is on the order -- coke is a seeded item that isn't.
    assert b'name="qty__chicken-biryani"' in response.data
    assert b'name="qty__coke"' not in response.data
    # coke should still be offered in the "add item" dropdown.
    assert b'value="coke"' in response.data


def test_admin_order_items_form_rejects_all_zero_never_500(admin_client, app, seeded_menu):
    order = _place_customer_order(app)
    response = admin_client.post(
        f"/admin/orders/{order['order_id']}/items", data={"qty__chicken-biryani": "0", "qty__coke": "0"}
    )
    assert response.status_code == 400
    assert b"at least one item" in response.data


def test_admin_can_create_order_via_form(admin_client, app, seeded_menu):
    response = admin_client.post(
        "/admin/orders/new",
        data={
            "customer_name": "Phone Customer",
            "mobile": "9876543212",
            "qty__chicken-biryani": "1",
            "qty__coke": "2",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/orders/ORD-")
    with app.app_context():
        db = get_db()
        order = db.orders.find_one({"customer_name": "Phone Customer"})
    assert order["status"] == "PROCESSING_FOOD"
    assert order["payment_status"] == "DUE"
    assert order["created_by"] == "admin"
    assert order["total"] == 280 + 60 * 2


def test_admin_create_order_form_shows_error_on_empty_items_never_500(admin_client, seeded_menu):
    response = admin_client.post(
        "/admin/orders/new", data={"customer_name": "Nobody", "mobile": "9876543213"}
    )
    assert response.status_code == 400
    assert b"at least one item" in response.data


def test_order_new_page_shows_searchable_add_item_ui_not_full_list(admin_client, seeded_menu):
    """The new-order page must not dump every menu item with a quantity box
    each -- it's the same one-at-a-time searchable-dropdown + Add button
    pattern as the order detail page's "Add item" control."""
    response = admin_client.get("/admin/orders/new")
    assert response.status_code == 200
    body = response.data.decode()
    assert 'id="add_item_id"' in body
    assert "tom-select" in body
    assert 'id="add_item_button"' in body
    assert ">Add<" in body
    assert "No items added yet." in body
    # No qty__<item_id> input should be pre-rendered for every item up
    # front -- only added once the admin actually picks it.
    assert 'name="qty__chicken-biryani"' not in body
    assert 'name="qty__coke"' not in body


def test_order_new_page_add_button_guards_against_tom_select_blur_swallowing_click(admin_client, seeded_menu):
    """Regression guard: Tom Select blurs its control on any mousedown
    outside it, which -- for the Add button sitting right next to the
    dropdown in the same flex row -- silently swallowed the very first
    click (it took two clicks to actually add an item) unless the button's
    own mousedown handler stops that from happening first."""
    response = admin_client.get("/admin/orders/new")
    assert response.status_code == 200
    body = response.data.decode()
    add_button_pos = body.index('id="add_item_button"')
    mousedown_pos = body.index('addItemButton.addEventListener("mousedown"')
    assert mousedown_pos > add_button_pos
    assert "event.preventDefault();" in body
    assert "event.stopPropagation();" in body
    # clear() must run before removeOption() for the same underlying reason
    # (Tom Select can't fully remove an option that's still selected).
    clear_pos = body.index("itemPicker.clear();")
    remove_option_pos = body.index("itemPicker.removeOption(itemId);")
    assert clear_pos < remove_option_pos


def test_order_new_page_validation_error_preserves_picked_items(admin_client, seeded_menu):
    """A validation error (e.g. missing customer name) must re-render the
    already-picked items instead of losing them, and must not re-offer an
    already-picked item in the dropdown (would let a resubmit produce a
    second qty__<id> field for the same item)."""
    response = admin_client.post(
        "/admin/orders/new",
        data={
            "customer_name": "",
            "mobile": "9876543212",
            "qty__chicken-biryani": "2",
            "note__chicken-biryani": "extra spicy",
        },
    )
    assert response.status_code == 400
    body = response.data.decode()
    assert 'name="qty__chicken-biryani" value="2"' in body
    assert 'name="note__chicken-biryani" value="extra spicy"' in body
    assert "Chicken Biryani" in body
    assert '<option value="chicken-biryani"' not in body
    assert '<option value="coke"' in body
