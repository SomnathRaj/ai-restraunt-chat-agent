"""Order creation, ID generation, and status lookup (PRD Sections 33-39).

Called from both app/api/orders.py (REST) and app/ai/tool_executor.py
(Gemini tool calls) -- never re-implement this logic in either caller.
"""

import re
from datetime import datetime, timezone

from flask import current_app
from pymongo import ReturnDocument

from app.models.db import get_db
from app.services import cart_service, menu_service, session_service
from app.utils.errors import AppError
from app.utils.pagination import paginate
from app.utils.sanitization import clean_text
from app.utils.validators import validate_customer_name, validate_mobile_number


def next_order_sequence(date_str: str) -> int:
    """Atomically increment and return today's order sequence number.

    One `find_one_and_update($inc)` per call is atomic server-side in
    MongoDB, so this is race-safe across concurrent Flask requests/workers
    with no external lock service (see ARCHITECTURE.md Section 4).
    """
    db = get_db()
    doc = db.counters.find_one_and_update(
        {"_id": f"order_seq_{date_str}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


def generate_order_id() -> str:
    """Return a unique order ID like ORD-20260919-4821 (PRD Section 35)."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    seq = next_order_sequence(date_str)
    return f"ORD-{date_str}-{seq:04d}"


def _build_order_items(cart: dict) -> tuple[list[dict], list[str]]:
    """Revalidate every cart line against live menu data (PRD Section 34).

    Prices/availability are refreshed from menu_service right now -- never
    trust the cart's possibly-stale snapshot, since availability or price
    may have changed since the item was added. Returns (order_items,
    unavailable_names); order_items is only reliable when unavailable is
    empty.
    """
    order_items = []
    unavailable = []
    for line in cart.get("items", []):
        try:
            menu_item = menu_service.get_menu_item(line["item_id"])
        except AppError:
            unavailable.append(line["name"])
            continue
        if not menu_item.get("availability"):
            unavailable.append(menu_item["name"])
            continue

        price = menu_item["price"]
        quantity = line["quantity"]
        order_items.append(
            {
                "item_id": line["item_id"],
                "name": menu_item["name"],
                "quantity": quantity,
                "price": price,
                "total": price * quantity,
                "special_instructions": line.get("instructions"),
            }
        )
    return order_items, unavailable


def preview_order(session_id: str) -> dict:
    """Validate and calculate the final order without creating it (PRD Section 31-32).

    Read-only and side-effect-free -- safe to call at any point, unlike
    create_order which is gated (see below).
    """
    session = session_service.get_or_create(session_id)
    cart = session.get("cart") or {}
    order_items, unavailable = _build_order_items(cart)
    subtotal = sum(item["total"] for item in order_items)
    return {
        "items": order_items,
        "order_notes": cart.get("order_notes"),
        "subtotal": subtotal,
        "total": subtotal,
        "unavailable_items": unavailable,
    }


def create_order(session_id: str, customer_name: str, mobile: str) -> dict:
    """Create a confirmed order after explicit customer confirmation (PRD Section 33-34).

    Refuses to run unless the session's checkout_flags.instructions_prompted
    is true -- the customer must have been asked about cooking instructions
    before an order can be placed (PRD Section 27/48, ARCHITECTURE.md Section 5).
    This flag is set either by cart_service when the customer volunteers an
    instruction, or by the mark_instructions_prompted tool once the AI has
    asked and received any answer -- see app/ai/tool_schemas.py.

    Revalidates product availability and current prices immediately before
    creation (PRD Section 34) -- refuses to create the order if anything in
    the cart has become unavailable since it was added, rather than silently
    dropping or substituting items.
    """
    name = validate_customer_name(customer_name)
    validated_mobile = validate_mobile_number(mobile)

    session = session_service.get_or_create(session_id)
    checkout_flags = session["conversation_context"]["checkout_flags"]
    if not checkout_flags.get("instructions_prompted"):
        raise AppError(
            "instructions_not_prompted",
            "Ask the customer about cooking instructions before placing the order.",
        )

    cart = session.get("cart") or {}
    order_items, unavailable = _build_order_items(cart)

    # Check unavailability BEFORE emptiness: if every cart line turned out
    # to be unavailable, order_items is empty too, but that is a very
    # different situation from a cart that had nothing in it to begin with
    # -- the customer needs to hear "these items are unavailable," not
    # "your cart is empty" (a real bug caught by live-testing this exact
    # scenario against the real Gemini API).
    if unavailable:
        raise AppError(
            "items_unavailable",
            f"These items became unavailable and must be resolved before placing the order: {', '.join(unavailable)}.",
            409,
        )

    if not order_items:
        raise AppError("cart_empty", "The cart is empty -- add at least one item before placing an order.")

    subtotal = sum(item["total"] for item in order_items)
    order_id = generate_order_id()
    now = datetime.now(timezone.utc)

    order_doc = {
        "order_id": order_id,
        "customer_name": name,
        "mobile": validated_mobile,
        "items": order_items,
        "order_notes": cart.get("order_notes"),
        "subtotal": subtotal,
        # V1: Total = Subtotal, no tax/service charge/discount (PRD Section 26).
        "total": subtotal,
        "status": "PENDING",
        "payment_status": "DUE",
        "created_by": "customer",
        "created_at": now,
        "updated_at": now,
    }

    db = get_db()
    db.orders.insert_one(order_doc)

    session_service.set_customer_info(session_id, name, validated_mobile)
    cart_service.clear_cart(session_id)

    order_doc.pop("_id", None)
    return order_doc


def get_order_status(order_id: str) -> dict:
    """Return the actual order status from MongoDB (PRD Section 37-39). Never guessed.

    Returns only order_id/status/items/order_notes/subtotal/total -- never
    customer_name/mobile, so knowing an order_id can't leak another
    customer's PII (ARCHITECTURE.md Section 9, PRD Section 80).
    """
    db = get_db()
    order = db.orders.find_one(
        {"order_id": order_id},
        {"_id": 0, "order_id": 1, "status": 1, "items": 1, "order_notes": 1, "subtotal": 1, "total": 1},
    )
    if order is None:
        raise AppError("order_not_found", f"No order found with ID '{order_id}'.", 404)
    return order


# ---------------------------------------------------------------------------
# Admin portal (PRD Section 92) -- called only from app/admin/orders.py.
# ---------------------------------------------------------------------------

_VALID_STATUSES = ("PENDING", "PROCESSING_FOOD", "COMPLETED", "CANCEL")
_VALID_PAYMENT_STATUSES = ("DUE", "PAID")


def list_orders_page(query: str = "", page: int = 1, page_size: int = 20) -> dict:
    """Return one page of orders, most recent first, optionally text-filtered.

    Searches order_id/customer_name/mobile/item names. order_id
    (ORD-YYYYMMDD-NNNN, Section 35) sorts lexicographically the same as
    chronologically, since the date and sequence are both fixed-width and
    zero-padded -- so plain `sort("order_id", -1)` gives "most recent
    first" with no extra index beyond the existing unique one.
    """
    db = get_db()
    query = (query or "").strip()
    mongo_filter = {}
    if query:
        pattern = re.escape(query)
        mongo_filter = {
            "$or": [
                {"order_id": {"$regex": pattern, "$options": "i"}},
                {"customer_name": {"$regex": pattern, "$options": "i"}},
                {"mobile": {"$regex": pattern, "$options": "i"}},
                {"items.name": {"$regex": pattern, "$options": "i"}},
            ]
        }

    total_count = db.orders.count_documents(mongo_filter)
    meta = paginate(total_count, page, page_size)
    cursor = db.orders.find(mongo_filter, {"_id": 0}).sort("order_id", -1).skip(meta["skip"]).limit(meta["page_size"])
    return {**meta, "orders": list(cursor)}


def get_order_for_admin(order_id: str) -> dict:
    """Return full order detail, including customer_name/mobile/payment_status/created_by.

    Unlike get_order_status() above, which deliberately withholds PII from
    the customer/AI-facing path, the admin is allowed to see everything.
    """
    db = get_db()
    order = db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if order is None:
        raise AppError("order_not_found", f"No order found with ID '{order_id}'.", 404)
    return order


def update_order_status(order_id: str, status: str) -> dict:
    """Set an order's status (PRD Section 92). Raises AppError on an invalid value."""
    if status not in _VALID_STATUSES:
        raise AppError("invalid_status", f"Status must be one of {', '.join(_VALID_STATUSES)}.", 400)

    db = get_db()
    result = db.orders.find_one_and_update(
        {"order_id": order_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise AppError("order_not_found", f"No order found with ID '{order_id}'.", 404)
    return result


def update_payment_status(order_id: str, payment_status: str) -> dict:
    """Set an order's payment_status (PRD Section 92). Raises AppError on an invalid value."""
    if payment_status not in _VALID_PAYMENT_STATUSES:
        raise AppError(
            "invalid_payment_status", f"Payment status must be one of {', '.join(_VALID_PAYMENT_STATUSES)}.", 400
        )

    db = get_db()
    result = db.orders.find_one_and_update(
        {"order_id": order_id},
        {"$set": {"payment_status": payment_status, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise AppError("order_not_found", f"No order found with ID '{order_id}'.", 404)
    return result


def mark_kt_generated(order_id: str) -> dict:
    """Advance an order from PENDING to PROCESSING_FOOD when its Kitchen
    Token is generated (PRD Section 92) -- printing the KT is the signal
    that the kitchen has been told to start cooking.

    Only ever transitions FROM PENDING. Reprinting a ticket for an order
    already in PROCESSING_FOOD/COMPLETED/CANCEL must never reset or
    downgrade its real status, so every other status is left untouched.
    Raises AppError 404 if the order doesn't exist.
    """
    order = get_order_for_admin(order_id)
    if order["status"] == "PENDING":
        order = update_order_status(order_id, "PROCESSING_FOOD")
    return order


def _resolve_order_items(items: list[dict]) -> list[dict]:
    """Turn admin-submitted {item_id, quantity, special_instructions} lines into
    real order items, with price/name looked up fresh from menu_service --
    never trusts a price from the admin form, for the same reason
    create_order() above never trusts one from the cart (PRD Section 25).
    """
    if not items:
        raise AppError("order_empty", "An order must have at least one item.", 400)

    resolved = []
    for line in items:
        try:
            quantity = int(line.get("quantity"))
        except (TypeError, ValueError):
            raise AppError("invalid_quantity", "Quantity must be a whole number.", 400)
        if quantity < 1:
            raise AppError("invalid_quantity", "Quantity must be at least 1.", 400)

        menu_item = menu_service.get_menu_item(line.get("item_id"))  # raises AppError 404 if missing/inactive
        price = menu_item["price"]
        instructions = (line.get("special_instructions") or "").strip() or None
        resolved.append(
            {
                "item_id": line["item_id"],
                "name": menu_item["name"],
                "quantity": quantity,
                "price": price,
                "total": price * quantity,
                "special_instructions": instructions,
            }
        )
    return resolved


def update_order_items(order_id: str, items: list[dict]) -> dict:
    """Replace an order's line items and recalculate subtotal/total (PRD Section 92).

    Takes the full new item list (not an incremental add/remove) -- the
    admin edit form always submits the complete, current state.
    """
    order_items = _resolve_order_items(items)
    subtotal = sum(item["total"] for item in order_items)

    db = get_db()
    result = db.orders.find_one_and_update(
        {"order_id": order_id},
        {"$set": {"items": order_items, "subtotal": subtotal, "total": subtotal, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise AppError("order_not_found", f"No order found with ID '{order_id}'.", 404)
    return result


def update_order_notes(order_id: str, order_notes) -> dict:
    """Update an order's whole-order cooking/preparation notes (PRD Section 68, 92).

    Sanitized the same way cart_service.set_order_notes cleans a customer's
    notes -- free text, never influences price/total/availability.
    """
    cleaned = clean_text(order_notes, current_app.config["INSTRUCTIONS_MAX_LENGTH"]) or None

    db = get_db()
    result = db.orders.find_one_and_update(
        {"order_id": order_id},
        {"$set": {"order_notes": cleaned, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise AppError("order_not_found", f"No order found with ID '{order_id}'.", 404)
    return result


def create_order_by_admin(items: list[dict], customer_name: str, mobile: str) -> dict:
    """Create an order directly, e.g. for a phone-in order (PRD Section 92).

    Starts at status PROCESSING_FOOD / payment_status DUE, created_by
    "admin" -- skips PENDING and the whole cart/checkout-gate machinery in
    create_order() above, since that gate exists to keep the *AI* honest,
    not because a trusted admin needs to go through it too.
    """
    name = validate_customer_name(customer_name)
    validated_mobile = validate_mobile_number(mobile)
    order_items = _resolve_order_items(items)
    subtotal = sum(item["total"] for item in order_items)

    order_id = generate_order_id()
    now = datetime.now(timezone.utc)
    order_doc = {
        "order_id": order_id,
        "customer_name": name,
        "mobile": validated_mobile,
        "items": order_items,
        "order_notes": None,
        "subtotal": subtotal,
        "total": subtotal,
        "status": "PROCESSING_FOOD",
        "payment_status": "DUE",
        "created_by": "admin",
        "created_at": now,
        "updated_at": now,
    }

    db = get_db()
    db.orders.insert_one(order_doc)
    order_doc.pop("_id", None)
    return order_doc
