"""Admin order management (PRD Section 92).

Every mutation goes through app/services/order_service.py, the same
service the customer/AI checkout path uses for order creation and status
lookup (ARCHITECTURE.md Section 12) -- status/payment_status/items edits
are admin-only additions on top of it, not a second implementation.
"""

from flask import current_app, redirect, render_template, request, url_for

from app.admin import bp
from app.services import menu_service, order_service
from app.utils.errors import AppError
from app.utils.upi_qr import build_upi_payment_uri, generate_qr_code_data_uri


def _active_menu_items():
    return [item for item in menu_service.list_all_menu_items() if item["active"]]


def _parse_items_from_form(menu_items):
    """Used by order_new: menu_items is the full active-item picker list,
    so every line the admin ticked a quantity for is a candidate."""
    items = []
    for item in menu_items:
        item_id = item["item_id"]
        raw_qty = request.form.get(f"qty__{item_id}", "").strip()
        if not raw_qty:
            continue
        try:
            quantity = int(raw_qty)
        except ValueError:
            continue
        if quantity <= 0:
            continue
        note = request.form.get(f"note__{item_id}", "").strip() or None
        items.append({"item_id": item_id, "quantity": quantity, "special_instructions": note})
    return items


def _parse_current_items_from_form(order):
    """Used by order_update_items: the order-detail page only renders a
    qty__<item_id>/note__<item_id> pair for items already on the order
    (PRD Section 92 -- the picker no longer lists the whole menu here)."""
    items = []
    for line in order["items"]:
        item_id = line["item_id"]
        raw_qty = request.form.get(f"qty__{item_id}", "").strip()
        if not raw_qty:
            continue
        try:
            quantity = int(raw_qty)
        except ValueError:
            continue
        if quantity <= 0:
            continue
        note = request.form.get(f"note__{item_id}", "").strip() or None
        items.append({"item_id": item_id, "quantity": quantity, "special_instructions": note})
    return items


def _parse_add_item_from_form():
    """The order-detail page's single 'add one more item' control."""
    item_id = request.form.get("add_item_id", "").strip()
    if not item_id:
        return None
    try:
        quantity = int(request.form.get("add_item_qty", "").strip())
    except ValueError:
        return None
    if quantity <= 0:
        return None
    note = request.form.get("add_item_note", "").strip() or None
    return {"item_id": item_id, "quantity": quantity, "special_instructions": note}


def _render_order_detail(order_id, error=None):
    order = order_service.get_order_for_admin(order_id)
    current_by_id = {line["item_id"]: line for line in order["items"]}
    menu_items = _active_menu_items()
    menu_by_id = {item["item_id"]: item for item in menu_items}
    addable_menu_items = [item for item in menu_items if item["item_id"] not in current_by_id]
    status_code = 400 if error else 200
    return (
        render_template(
            "admin/order_detail.html",
            order=order,
            current_by_id=current_by_id,
            menu_by_id=menu_by_id,
            addable_menu_items=addable_menu_items,
            error=error,
        ),
        status_code,
    )


@bp.get("/orders")
def orders_list():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    result = order_service.list_orders_page(query, page)
    return render_template("admin/orders_list.html", query=query, **result)


@bp.get("/orders/<order_id>")
def order_detail(order_id):
    return _render_order_detail(order_id)


@bp.post("/orders/<order_id>/status")
def order_update_status(order_id):
    try:
        order_service.update_order_status(order_id, request.form.get("status", ""))
    except AppError as err:
        return _render_order_detail(order_id, error=err.message)
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<order_id>/payment-status")
def order_update_payment_status(order_id):
    try:
        order_service.update_payment_status(order_id, request.form.get("payment_status", ""))
    except AppError as err:
        return _render_order_detail(order_id, error=err.message)
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<order_id>/generate-kt")
def order_generate_kt(order_id):
    """Generate (or reprint) a Kitchen Token -- advances PENDING orders to
    PROCESSING_FOOD (PRD Section 92) then hands off to the print view."""
    order_service.mark_kt_generated(order_id)
    return redirect(url_for("admin.order_kt", order_id=order_id))


@bp.get("/orders/<order_id>/kt")
def order_kt(order_id):
    order = order_service.get_order_for_admin(order_id)
    return render_template("admin/order_kt.html", order=order)


@bp.get("/orders/<order_id>/invoice")
def order_invoice(order_id):
    """Printable customer invoice -- unlike the KT, this carries prices and
    has no status side effect, so it's a plain GET with no companion POST."""
    order = order_service.get_order_for_admin(order_id)

    upi_id = current_app.config.get("UPI_ID")
    upi_qr_data_uri = None
    if upi_id:
        upi_uri = build_upi_payment_uri(
            upi_id, current_app.config["RESTAURANT_NAME"], order["total"], order["order_id"]
        )
        upi_qr_data_uri = generate_qr_code_data_uri(upi_uri)

    return render_template(
        "admin/order_invoice.html", order=order, upi_id=upi_id, upi_qr_data_uri=upi_qr_data_uri
    )


@bp.post("/orders/<order_id>/items")
def order_update_items(order_id):
    order = order_service.get_order_for_admin(order_id)
    items = _parse_current_items_from_form(order)

    add_item = _parse_add_item_from_form()
    if add_item:
        existing = next((line for line in items if line["item_id"] == add_item["item_id"]), None)
        if existing:
            existing["quantity"] += add_item["quantity"]
        else:
            items.append(add_item)

    try:
        order_service.update_order_items(order_id, items)
        order_service.update_order_notes(order_id, request.form.get("order_notes", ""))
    except AppError as err:
        return _render_order_detail(order_id, error=err.message)
    return redirect(url_for("admin.order_detail", order_id=order_id))


def _render_order_new(menu_items, current_by_id, error, customer_name, mobile, status_code=200):
    menu_by_id = {item["item_id"]: item for item in menu_items}
    addable_menu_items = [item for item in menu_items if item["item_id"] not in current_by_id]
    # Compact, JS-side lookup (item_id -> name/price/is_veg) for building an
    # added-item row -- independent of the dropdown's current option list,
    # so it still works for a row already-added before a validation error
    # reload (and thus excluded from addable_menu_items above).
    menu_items_json = [
        {"item_id": item["item_id"], "name": item["name"], "price": item["price"], "is_veg": item["is_veg"]}
        for item in menu_items
    ]
    return (
        render_template(
            "admin/order_new.html",
            menu_by_id=menu_by_id,
            current_by_id=current_by_id,
            addable_menu_items=addable_menu_items,
            menu_items_json=menu_items_json,
            error=error,
            customer_name=customer_name,
            mobile=mobile,
        ),
        status_code,
    )


@bp.route("/orders/new", methods=["GET", "POST"])
def order_new():
    menu_items = _active_menu_items()

    if request.method == "GET":
        return _render_order_new(menu_items, current_by_id={}, error=None, customer_name="", mobile="")

    customer_name = request.form.get("customer_name", "")
    mobile = request.form.get("mobile", "")
    items = _parse_items_from_form(menu_items)
    try:
        order = order_service.create_order_by_admin(items, customer_name, mobile)
    except AppError as err:
        current_by_id = {line["item_id"]: line for line in items}
        return _render_order_new(
            menu_items, current_by_id=current_by_id, error=err.message, customer_name=customer_name, mobile=mobile, status_code=400
        )

    return redirect(url_for("admin.order_detail", order_id=order["order_id"]))
