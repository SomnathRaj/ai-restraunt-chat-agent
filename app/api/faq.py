"""FAQ API (PRD Section 44). Thin HTTP layer only -- all logic in faq_service."""

from flask import Blueprint, jsonify, request

from app.services import faq_service
from app.utils.errors import AppError

bp = Blueprint("faq", __name__, url_prefix="/api/faq")


@bp.get("/search")
def search_faq():
    query = request.args.get("q", "")
    if not query.strip():
        raise AppError("invalid_request", "q is required")
    return jsonify(faq_service.search_faq(query))
