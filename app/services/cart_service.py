"""Temporary cart management, including cooking instructions (PRD Sections 22-27).

Called from both app/api/cart.py (REST -- e.g. the [Add] button) and
app/ai/tool_executor.py (AI tool calls) -- never re-implement this logic
in either caller. Every mutation must re-validate against menu_service
(product exists/active/available, price from MongoDB) per PRD Section 24.

Cart shape (stored on the chat_sessions doc):
    {
        "items": [
            {"item_id": str, "name": str, "price": number, "quantity": int,
             "instructions": str | None},
            ...
        ],
        "order_notes": str | None,
    }
"""

from datetime import datetime, timezone

from flask import current_app

from app.models.db import get_db
from app.services import menu_service, session_service
from app.utils.errors import AppError
from app.utils.sanitization import clean_text

_EMPTY_CART = {"items": [], "order_notes": None}


def _validate_quantity(quantity, *, allow_zero: bool) -> int:
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise AppError("invalid_quantity", "Quantity must be a whole number.")

    minimum = 0 if allow_zero else 1
    maximum = current_app.config["MAX_ITEM_QUANTITY"]
    if not (minimum <= quantity <= maximum):
        raise AppError("invalid_quantity", f"Quantity must be between {minimum} and {maximum}.")
    return quantity


def _get_session_cart(session_id: str) -> dict:
    session = session_service.get_or_create(session_id)
    return session.get("cart") or dict(_EMPTY_CART)


def _with_totals(cart: dict) -> dict:
    items = cart.get("items", [])
    subtotal = sum(item["price"] * item["quantity"] for item in items)
    # V1: Total = Subtotal, no tax/service charge/discount (PRD Section 26).
    return {"items": items, "order_notes": cart.get("order_notes"), "subtotal": subtotal, "total": subtotal}


def _save_session_cart(session_id: str, cart: dict) -> dict:
    db = get_db()
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"cart": cart, "updated_at": datetime.now(timezone.utc)}},
    )
    return _with_totals(cart)


def _find_line(cart: dict, ref: str) -> dict | None:
    """The cart line whose item_id is `ref`, else whose name equals `ref` ignoring case.

    Accepting the name lets the AI add an item and set its instructions in
    the SAME turn -- before it has seen the item_id add_to_cart returns.
    """
    ref = (ref or "").strip()
    lines = cart.get("items", [])
    for line in lines:
        if line["item_id"] == ref:
            return line
    named = [line for line in lines if line["name"].casefold() == ref.casefold()]
    return named[0] if len(named) == 1 else None


def get_cart(session_id: str) -> dict:
    """Return the current cart for a session (PRD Section 22)."""
    return _with_totals(_get_session_cart(session_id))


def add_to_cart(session_id: str, item_id: str, quantity: int) -> dict:
    """Validate the item against menu_service, then add/merge into the cart (PRD Section 20, 24).

    `item_id` may also be the item's exact menu name (see menu_service.find_item).
    Price always comes from menu_service (i.e. MongoDB) -- there is no price
    parameter here for a caller to supply (PRD Section 25).
    """
    quantity = _validate_quantity(quantity, allow_zero=False)

    item = menu_service.resolve_item(item_id)  # raises AppError 404 (with suggestions) if missing/inactive
    if not item.get("availability"):
        raise AppError("item_unavailable", f"{item['name']} is currently unavailable.")
    item_id = item["item_id"]

    cart = _get_session_cart(session_id)
    line = _find_line(cart, item_id)
    if line is not None:
        line["quantity"] += quantity
        line["price"] = item["price"]
        line["name"] = item["name"]
    else:
        cart.setdefault("items", []).append(
            {
                "item_id": item_id,
                "name": item["name"],
                "price": item["price"],
                "quantity": quantity,
                "instructions": None,
            }
        )

    return _save_session_cart(session_id, cart)


def remove_from_cart(session_id: str, item_id: str) -> dict:
    """Remove an item (by item_id or exact name) from the cart (PRD Section 23). No-op if it isn't there."""
    cart = _get_session_cart(session_id)
    line = _find_line(cart, item_id)
    if line is not None:
        cart["items"] = [other for other in cart["items"] if other is not line]
    return _save_session_cart(session_id, cart)


def update_cart_quantity(session_id: str, item_id: str, quantity: int) -> dict:
    """Change an item's quantity, or remove it if quantity reaches 0 (PRD Section 23)."""
    quantity = _validate_quantity(quantity, allow_zero=True)
    if quantity == 0:
        return remove_from_cart(session_id, item_id)

    cart = _get_session_cart(session_id)
    line = _find_line(cart, item_id)
    if line is None:
        raise AppError("item_not_in_cart", f"'{item_id}' is not in the cart.", 404)

    line["quantity"] = quantity
    return _save_session_cart(session_id, cart)


def clear_cart(session_id: str) -> dict:
    """Empty the cart (PRD Section 23)."""
    return _save_session_cart(session_id, dict(_EMPTY_CART))


def set_item_instructions(session_id: str, item_id: str, instructions: str) -> dict:
    """Attach a free-text cooking/preparation note to one cart item (PRD Section 27).

    Must never change price, quantity, or availability -- this function's
    only side effect is setting the `instructions` string on the matching
    cart line. See ARCHITECTURE.md Section 5 for why this separation matters.

    Also satisfies order_service.create_order's checkout gate -- a customer
    who volunteers an instruction has, by definition, already addressed the
    "any cooking instructions?" question, so the AI shouldn't need to ask
    again (PRD Section 27).
    """
    cart = _get_session_cart(session_id)
    line = _find_line(cart, item_id)
    if line is None:
        raise AppError("item_not_in_cart", f"'{item_id}' is not in the cart.", 404)

    cleaned = clean_text(instructions, current_app.config["INSTRUCTIONS_MAX_LENGTH"])
    line["instructions"] = cleaned or None
    session_service.mark_instructions_prompted(session_id)
    return _save_session_cart(session_id, cart)


def set_order_notes(session_id: str, instructions: str) -> dict:
    """Attach a free-text cooking/preparation note to the whole order (PRD Section 27).

    Also satisfies order_service.create_order's checkout gate -- see
    set_item_instructions above.
    """
    cart = _get_session_cart(session_id)
    cleaned = clean_text(instructions, current_app.config["INSTRUCTIONS_MAX_LENGTH"])
    cart["order_notes"] = cleaned or None
    session_service.mark_instructions_prompted(session_id)
    return _save_session_cart(session_id, cart)


def calculate_order_total(session_id: str) -> dict:
    """Return {items, subtotal, total} computed from MongoDB prices (PRD Section 25-26)."""
    return _with_totals(_get_session_cart(session_id))
