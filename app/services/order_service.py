"""Order creation, ID generation, and status lookup (PRD Sections 33-39).

Called from both app/api/orders.py (REST) and app/ai/tool_executor.py
(Gemini tool calls) -- never re-implement this logic in either caller.
"""

from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.models.db import get_db
from app.services import cart_service, menu_service, session_service
from app.utils.errors import AppError
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
