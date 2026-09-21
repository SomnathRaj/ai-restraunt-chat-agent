"""Menu API (PRD Section 43). Thin HTTP layer only -- all logic in menu_service."""

from flask import Blueprint, jsonify, request

from app.services import menu_service

bp = Blueprint("menu", __name__, url_prefix="/api/menu")


@bp.get("")
def get_menu():
    return jsonify(menu_service.get_available_menu())


@bp.get("/search")
def search_menu():
    query = request.args.get("q", "")
    return jsonify(menu_service.search_menu(query))


@bp.get("/<item_id>")
def get_menu_item(item_id: str):
    return jsonify(menu_service.get_menu_item(item_id))
