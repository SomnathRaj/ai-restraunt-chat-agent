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

import re
from datetime import datetime, timezone

from flask import current_app

from app.models.db import get_db
from app.utils.errors import AppError
from app.utils.pagination import paginate


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


# ---------------------------------------------------------------------------
# Admin portal (PRD Section 93) -- called only from app/admin/sessions.py.
# ---------------------------------------------------------------------------


def list_sessions_page(query: str = "", page: int = 1, page_size: int = 20) -> dict:
    """Return one page of the most recently active sessions, optionally
    text-filtered by customer_name/mobile only.

    The projection excludes conversation_context/cart entirely -- the list
    view is structurally incapable of leaking message content or cart
    contents, not just "the template happens not to render them" (PRD
    Section 93's privacy requirement). Search is restricted to the same two
    fields for the same reason: never lets a query filter on message text
    that the list view itself is deliberately never given.
    """
    db = get_db()
    query = (query or "").strip()
    mongo_filter = {}
    if query:
        pattern = re.escape(query)
        mongo_filter = {
            "$or": [
                {"customer_name": {"$regex": pattern, "$options": "i"}},
                {"mobile": {"$regex": pattern, "$options": "i"}},
            ]
        }

    total_count = db.chat_sessions.count_documents(mongo_filter)
    meta = paginate(total_count, page, page_size)
    cursor = (
        db.chat_sessions.find(
            mongo_filter, {"_id": 0, "session_id": 1, "customer_name": 1, "mobile": 1, "updated_at": 1}
        )
        .sort("updated_at", -1)
        .skip(meta["skip"])
        .limit(meta["page_size"])
    )
    return {**meta, "sessions": list(cursor)}


def get_session_history(session_id: str) -> dict:
    """Return one session's full transcript for the admin (PRD Section 93).

    Never auto-creates (unlike get_or_create above) -- an admin looking up
    a session_id that doesn't exist should see a 404, not silently create
    an empty session doc.
    """
    db = get_db()
    session = db.chat_sessions.find_one(
        {"session_id": session_id},
        {
            "_id": 0,
            "session_id": 1,
            "customer_name": 1,
            "mobile": 1,
            "created_at": 1,
            "updated_at": 1,
            "conversation_context.history": 1,
        },
    )
    if session is None:
        raise AppError("session_not_found", f"No chat session found with id '{session_id}'.", 404)
    session["history"] = session.pop("conversation_context", {}).get("history", [])
    return session


def delete_session(session_id: str) -> None:
    """Delete a chat session (PRD Section 93).

    Only ever removes the chat_sessions document -- orders are a separate,
    permanent collection precisely so they survive session cleanup
    (Section 68), whether that cleanup is this admin action or the TTL
    index's automatic idle expiry.
    """
    db = get_db()
    result = db.chat_sessions.delete_one({"session_id": session_id})
    if result.deleted_count == 0:
        raise AppError("session_not_found", f"No chat session found with id '{session_id}'.", 404)
