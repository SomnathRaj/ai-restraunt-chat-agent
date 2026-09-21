"""Chat session lifecycle: creation, retrieval, bounded history, checkout flags
(PRD Sections 42, 69; ARCHITECTURE.md Section 3).

Session doc shape (chat_sessions collection):
    {
        "session_id": str,
        "language": "english" | "bengali" | "hinglish" | "benglish" | "mixed",
        "cart": {...},                       # see cart_service module docstring
        "customer_name": str | None,
        "mobile": str | None,
        "conversation_context": {
            "history": [{"role": "user" | "model", "text": str, "ts": datetime}],
            "checkout_flags": {
                "instructions_prompted": bool,
                "instructions_declined": bool,
            },
        },
        "created_at": datetime,
        "updated_at": datetime,
    }

A MongoDB TTL index on updated_at (see app/models/db.py) gives sessions a
24h sliding idle expiry -- completed orders are copied into their own
`orders` document and are unaffected by session expiry.
"""

from datetime import datetime, timezone

from flask import current_app

from app.models.db import get_db


def _default_session(session_id: str, now: datetime) -> dict:
    return {
        "session_id": session_id,
        "language": None,
        "cart": {"items": [], "order_notes": None},
        "customer_name": None,
        "mobile": None,
        "conversation_context": {
            "history": [],
            "checkout_flags": {"instructions_prompted": False, "instructions_declined": False},
        },
        "created_at": now,
        "updated_at": now,
    }


def get_or_create(session_id: str) -> dict:
    """Load a session doc, creating one with empty cart/history on first use."""
    db = get_db()
    now = datetime.now(timezone.utc)
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {"$setOnInsert": _default_session(session_id, now)},
        upsert=True,
    )
    return db.chat_sessions.find_one({"session_id": session_id})


def append_turn(session_id: str, user_text: str, model_text: str) -> None:
    """Push a user/model turn pair onto the bounded rolling history window.

    Caps history at HISTORY_MAX_MESSAGES (config.py) via $slice, and always
    bumps updated_at (renewing the TTL expiry).
    """
    get_or_create(session_id)
    db = get_db()
    now = datetime.now(timezone.utc)
    turns = [
        {"role": "user", "text": user_text, "ts": now},
        {"role": "model", "text": model_text, "ts": now},
    ]
    max_messages = current_app.config["HISTORY_MAX_MESSAGES"]
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$push": {"conversation_context.history": {"$each": turns, "$slice": -max_messages}},
            "$set": {"updated_at": now},
        },
    )


def set_customer_info(session_id: str, name: str, mobile: str) -> None:
    """Persist validated customer name/mobile onto the session (PRD Section 28-30)."""
    get_or_create(session_id)
    db = get_db()
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"customer_name": name, "mobile": mobile, "updated_at": datetime.now(timezone.utc)}},
    )


def mark_instructions_prompted(session_id: str) -> None:
    """Set checkout_flags.instructions_prompted -- gates order_service.create_order."""
    get_or_create(session_id)
    db = get_db()
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "conversation_context.checkout_flags.instructions_prompted": True,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


def mark_instructions_declined(session_id: str) -> None:
    """Set checkout_flags.instructions_declined when the customer opts out."""
    get_or_create(session_id)
    db = get_db()
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "conversation_context.checkout_flags.instructions_declined": True,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
