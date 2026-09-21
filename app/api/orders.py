"""Orders API (PRD Section 44). Thin HTTP layer only -- all logic in order_service."""

from flask import Blueprint, jsonify, request

from app.services import order_service
from app.utils.errors import AppError

bp = Blueprint("orders", __name__, url_prefix="/api/orders")


@bp.post("/preview")
def preview_order():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    if not session_id:
        raise AppError("invalid_request", "session_id is required")
    return jsonify(order_service.preview_order(session_id))


@bp.post("")
def create_order():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    if not session_id:
        raise AppError("invalid_request", "session_id is required")

    # Name/mobile validation lives in order_service.create_order itself, so
    # the AI tool-call path and this REST path get identical validation --
    # neither caller re-implements it (ARCHITECTURE.md Section 5's rule).
    return jsonify(order_service.create_order(session_id, body.get("customer_name", ""), body.get("mobile", "")))


@bp.get("/<order_id>")
def get_order_status(order_id: str):
    return jsonify(order_service.get_order_status(order_id))
