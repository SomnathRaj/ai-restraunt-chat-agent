"""Chat API (PRD Section 44-45) -- the primary AI conversation endpoint."""

from flask import Blueprint, current_app, jsonify, request

from app.ai.agent import ChatAgent
from app.ai.providers.base import AIProviderNotConfigured, AIProviderRateLimited, AIProviderUnavailable
from app.extensions import limiter
from app.utils.errors import AppError, error_response
from app.utils.sanitization import clean_text

bp = Blueprint("chat", __name__, url_prefix="/api/chat")


@bp.post("")
# 10/minute, not a round-guess number: measured against the real Gemini free
# tier (15 requests/minute) and this model's observed behavior of up to
# TOOL_LOOP_MAX_ITERATIONS (8) API calls for a single busy chat turn -- our
# own limit is deliberately tighter than the old "20" so a legitimate but
# fast-typing customer hits our friendly 429 before silently burning through
# Gemini's entire quota via one script (see Phase 4/9 notes in CHECKLIST.md).
@limiter.limit("10 per minute")
def chat():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    raw_message = body.get("message", "")

    if not session_id:
        raise AppError("invalid_request", "session_id is required")

    message = clean_text(raw_message, current_app.config["MAX_MESSAGE_LENGTH"])
    if not message:
        raise AppError("invalid_request", "message must not be empty")

    # Each provider adapter maps its own SDK's failures onto these neutral
    # types and logs the details server-side, so raw SDK exceptions (which
    # can include response bodies) never reach the customer.
    try:
        reply = ChatAgent().handle_message(session_id, message)
    except AIProviderNotConfigured:
        return error_response(
            "ai_not_configured", "Our AI assistant isn't set up yet. Please try again later.", 503
        )
    except AIProviderRateLimited:
        return error_response(
            "ai_rate_limited", "We're getting a lot of requests right now. Please try again in a moment.", 429
        )
    except AIProviderUnavailable:
        return error_response(
            "ai_unavailable", "I'm having trouble reaching our AI service right now. Please try again in a moment.", 503
        )

    return jsonify({"session_id": session_id, "reply": reply})
