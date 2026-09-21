"""Cart API (PRD Section 44). Thin HTTP layer only -- all logic in cart_service.

This is the REST/UI path (e.g. the [Add] button on a menu card). It must
call the exact same cart_service functions as the AI tool-call path in
app/ai/tool_executor.py -- see ARCHITECTURE.md Section 5.
"""

from flask import Blueprint, jsonify, request

from app.services import cart_service
from app.utils.errors import AppError

bp = Blueprint("cart", __name__, url_prefix="/api/cart")

_ACTIONS = {"add", "remove", "update_quantity", "clear", "set_item_instructions", "set_order_notes"}


@bp.post("")
def mutate_cart():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    action = body.get("action")
    if not session_id or action not in _ACTIONS:
        raise AppError("invalid_request", f"action must be one of {sorted(_ACTIONS)}")

    if action == "add":
        result = cart_service.add_to_cart(session_id, body.get("item_id"), int(body.get("quantity", 1)))
    elif action == "remove":
        result = cart_service.remove_from_cart(session_id, body.get("item_id"))
    elif action == "update_quantity":
        result = cart_service.update_cart_quantity(session_id, body.get("item_id"), int(body.get("quantity", 0)))
    elif action == "clear":
        result = cart_service.clear_cart(session_id)
    elif action == "set_item_instructions":
        result = cart_service.set_item_instructions(session_id, body.get("item_id"), body.get("instructions", ""))
    else:  # set_order_notes
        result = cart_service.set_order_notes(session_id, body.get("instructions", ""))

    return jsonify(result)


@bp.get("/<session_id>")
def get_cart(session_id: str):
    return jsonify(cart_service.get_cart(session_id))
