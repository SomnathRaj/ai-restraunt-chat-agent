"""Chat API (PRD Section 44-45) -- the primary AI conversation endpoint."""

import logging

import httpx
from flask import Blueprint, current_app, jsonify, request
from google.genai.errors import APIError

from app.ai.agent import ChatAgent
from app.ai.gemini_client import GeminiNotConfigured
from app.extensions import limiter
from app.utils.errors import AppError, error_response
from app.utils.sanitization import clean_text

log = logging.getLogger(__name__)

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

    try:
        reply = ChatAgent().handle_message(session_id, message)
    except GeminiNotConfigured:
        return error_response("ai_not_configured", "Gemini is not configured yet. Set GEMINI_API_KEY in .env.", 503)
    except APIError as err:
        # Covers real failures we've hit during development: Gemini quota
        # exhaustion (429) and upstream 5xx errors. Never let the raw SDK
        # exception (which can include response bodies) reach the generic
        # 500 handler / customer -- log server-side only, reply honestly.
        log.warning("gemini_api_error", extra={"status_code": err.code})
        if err.code == 429:
            return error_response(
                "ai_rate_limited", "We're getting a lot of requests right now. Please try again in a moment.", 429
            )
        return error_response(
            "ai_unavailable", "I'm having trouble reaching our AI service right now. Please try again in a moment.", 503
        )
    except httpx.HTTPError as err:
        # Network-level failure below the API-response layer (connection
        # refused, DNS failure, or -- explicitly required by PRD Section 76
        # ("gracefully handle Gemini/API timeouts") -- a genuine timeout.
        log.warning("gemini_network_error", extra={"error_type": type(err).__name__})
        return error_response(
            "ai_unavailable", "I'm having trouble reaching our AI service right now. Please try again in a moment.", 503
        )

    return jsonify({"session_id": session_id, "reply": reply})
