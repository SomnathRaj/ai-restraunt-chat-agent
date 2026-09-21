"""Menu retrieval, search, and availability (PRD Sections 13-18).

Called from both app/api/menu.py (REST) and app/ai/tool_executor.py (Gemini
tool calls) -- never re-implement this logic in either caller.
"""

import re

from app.models.db import get_db
from app.utils.errors import AppError

# Internal bookkeeping fields never returned to the customer/AI.
_PUBLIC_PROJECTION = {"_id": 0, "active": 0, "created_at": 0, "updated_at": 0}


def get_available_menu() -> list[dict]:
    """Return all active, currently-available menu items (PRD Section 15)."""
    db = get_db()
    cursor = db.menu.find({"active": True, "availability": True}, _PUBLIC_PROJECTION)
    return list(cursor.sort([("category", 1), ("name", 1)]))


def search_menu(query: str) -> list[dict]:
    """Search available menu items by free-text query (PRD Section 17).

    Matches name/description/category/tags. Only active + available items
    are eligible -- an item the customer can't order shouldn't turn up in
    search results (PRD Section 14).
    """
    query = (query or "").strip()
    if not query:
        return []

    pattern = re.escape(query)
    text_match = {
        "$or": [
            {"name": {"$regex": pattern, "$options": "i"}},
            {"description": {"$regex": pattern, "$options": "i"}},
            {"category": {"$regex": pattern, "$options": "i"}},
            {"tags": {"$regex": pattern, "$options": "i"}},
        ]
    }

    db = get_db()
    cursor = db.menu.find({"active": True, "availability": True, **text_match}, _PUBLIC_PROJECTION)
    return list(cursor)


def get_menu_item(item_id: str) -> dict:
    """Return full details for one menu item (PRD Section 16). Raises AppError if not found.

    Not filtered by availability -- an out-of-stock item should still be
    look-up-able so the agent can explain its status (PRD Section 16).
    """
    db = get_db()
    doc = db.menu.find_one({"item_id": item_id, "active": True}, _PUBLIC_PROJECTION)
    if doc is None:
        raise AppError("item_not_found", f"No menu item found with id '{item_id}'.", 404)
    return doc


def check_item_availability(item_id: str) -> bool:
    """Return whether item_id is currently active and available (PRD Section 14).

    An unknown item_id is treated as unavailable rather than raising --
    this is a boolean predicate, not a lookup.
    """
    db = get_db()
    doc = db.menu.find_one({"item_id": item_id, "active": True}, {"availability": 1})
    return bool(doc and doc.get("availability"))


def get_alternatives(item_id: str, limit: int = 4) -> list[dict]:
    """Return relevant available alternatives for an unavailable/missing item (PRD Section 18).

    Ranked by: same category, then tag overlap, then price closeness --
    matching the ranking signals PRD Section 18 lists. Never invents
    alternatives; only returns active + available menu items.
    """
    db = get_db()
    target = db.menu.find_one({"item_id": item_id}, {"category": 1, "tags": 1, "price": 1})

    candidates = list(
        db.menu.find(
            {"active": True, "availability": True, "item_id": {"$ne": item_id}},
            _PUBLIC_PROJECTION,
        )
    )

    if target is None:
        return candidates[:limit]

    target_category = target.get("category")
    target_tags = set(target.get("tags") or [])
    target_price = target.get("price")

    def _rank(candidate: dict) -> tuple:
        different_category = 0 if candidate.get("category") == target_category else 1
        tag_overlap = len(target_tags & set(candidate.get("tags") or []))
        price_diff = abs((candidate.get("price") or 0) - (target_price or 0))
        return (different_category, -tag_overlap, price_diff)

    candidates.sort(key=_rank)
    return candidates[:limit]
