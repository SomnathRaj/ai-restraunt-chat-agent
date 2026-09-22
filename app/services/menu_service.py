"""Menu retrieval, search, and availability (PRD Sections 13-18).

Called from both app/api/menu.py (REST) and app/ai/tool_executor.py (Gemini
tool calls) -- never re-implement this logic in either caller.
"""

import re
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.models.db import get_db
from app.utils.errors import AppError
from app.utils.pagination import paginate
from app.utils.sanitization import parse_comma_list, slugify

# Internal bookkeeping fields never returned to the customer/AI.
_PUBLIC_PROJECTION = {"_id": 0, "active": 0, "created_at": 0, "updated_at": 0}

# The admin portal (PRD Section 91) needs every field, including the ones
# hidden from the customer/AI above -- it's the one caller allowed to see
# `active` and manage inactive/unavailable items.
_ADMIN_PROJECTION = {"_id": 0}


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


# ---------------------------------------------------------------------------
# Admin portal (PRD Section 91) -- called only from app/admin/menu.py.
# ---------------------------------------------------------------------------


def list_all_menu_items() -> list[dict]:
    """Return every menu item, including inactive/unavailable ones.

    Unlike get_available_menu(), this is for the admin list view -- an
    admin must be able to see and re-activate an item a customer would
    never be shown.
    """
    db = get_db()
    cursor = db.menu.find({}, _ADMIN_PROJECTION)
    return list(cursor.sort([("category", 1), ("name", 1)]))


def list_menu_items_page(query: str = "", page: int = 1, page_size: int = 20) -> dict:
    """Return one page of admin menu items, optionally text-filtered.

    Unlike list_all_menu_items() (used internally by pickers that need
    every item unpaginated), this is for the admin's own /admin/menu list
    view -- searches name/description/category/tags across ALL items
    (active or not), same fields the customer-facing search_menu() matches.
    """
    db = get_db()
    query = (query or "").strip()
    mongo_filter = {}
    if query:
        pattern = re.escape(query)
        mongo_filter = {
            "$or": [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"description": {"$regex": pattern, "$options": "i"}},
                {"category": {"$regex": pattern, "$options": "i"}},
                {"tags": {"$regex": pattern, "$options": "i"}},
            ]
        }

    total_count = db.menu.count_documents(mongo_filter)
    meta = paginate(total_count, page, page_size)
    cursor = (
        db.menu.find(mongo_filter, _ADMIN_PROJECTION)
        .sort([("category", 1), ("name", 1)])
        .skip(meta["skip"])
        .limit(meta["page_size"])
    )
    return {**meta, "items": list(cursor)}


def get_menu_item_for_admin(item_id: str) -> dict:
    """Return full details for one menu item regardless of active/availability.

    Raises AppError 404 if it doesn't exist at all.
    """
    db = get_db()
    doc = db.menu.find_one({"item_id": item_id}, _ADMIN_PROJECTION)
    if doc is None:
        raise AppError("item_not_found", f"No menu item found with id '{item_id}'.", 404)
    return doc


def list_categories() -> list[str]:
    """Return every predefined category name, for the admin menu form's dropdown.

    Categories are seeded directly into MongoDB (scripts/seed_categories.py)
    -- there is deliberately no admin UI to manage them, just this fixed
    reference list that create_menu_item/update_menu_item validate against.
    """
    db = get_db()
    return [doc["name"] for doc in db.categories.find({}, {"_id": 0, "name": 1}).sort("name", 1)]


def _unique_item_id(db, base_slug: str) -> str:
    candidate = base_slug
    suffix = 2
    while db.menu.find_one({"item_id": candidate}, {"_id": 1}):
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _parse_price(raw) -> float:
    try:
        price = float(raw)
    except (TypeError, ValueError):
        raise AppError("invalid_price", "Price must be a number.", 400)
    if price <= 0:
        raise AppError("invalid_price", "Price must be greater than zero.", 400)
    return price


def _validate_required_fields(db, name: str, category: str) -> tuple[str, str]:
    name = (name or "").strip()
    category = (category or "").strip()
    if not name:
        raise AppError("missing_name", "Name is required.", 400)
    if not category:
        raise AppError("missing_category", "Category is required.", 400)
    if db.categories.find_one({"name": category}) is None:
        raise AppError("invalid_category", f"'{category}' is not a valid category.", 400)
    return name, category


def create_menu_item(
    *, name: str, description: str, category: str, price, availability: bool, is_veg: bool, tags, image=None
) -> dict:
    """Create a new menu item (PRD Section 91). Raises AppError on invalid input.

    item_id is derived from the name (matching the seed script's slug
    convention) rather than taken from the admin -- it's an internal
    identifier, not something worth asking a human to type correctly.
    """
    db = get_db()
    name, category = _validate_required_fields(db, name, category)
    price = _parse_price(price)

    item_id = _unique_item_id(db, slugify(name))
    now = datetime.now(timezone.utc)
    doc = {
        "item_id": item_id,
        "name": name,
        "description": (description or "").strip(),
        "category": category,
        "price": price,
        "availability": bool(availability),
        "is_veg": bool(is_veg),
        "image": image or None,
        "tags": parse_comma_list(tags),
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    db.menu.insert_one(doc)
    doc.pop("_id", None)
    return doc


def update_menu_item(
    item_id: str, *, name: str, description: str, category: str, price, availability: bool, is_veg: bool, tags, image=None
) -> dict:
    """Update an existing menu item's editable fields (PRD Section 91).

    Never touches item_id/active/created_at -- those are managed
    separately (set_menu_item_active) or immutable.
    """
    db = get_db()
    name, category = _validate_required_fields(db, name, category)
    price = _parse_price(price)

    update = {
        "name": name,
        "description": (description or "").strip(),
        "category": category,
        "price": price,
        "availability": bool(availability),
        "is_veg": bool(is_veg),
        "image": image or None,
        "tags": parse_comma_list(tags),
        "updated_at": datetime.now(timezone.utc),
    }
    result = db.menu.find_one_and_update(
        {"item_id": item_id},
        {"$set": update},
        projection=_ADMIN_PROJECTION,
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise AppError("item_not_found", f"No menu item found with id '{item_id}'.", 404)
    return result


def set_menu_item_active(item_id: str, active: bool) -> None:
    """Activate/deactivate an item without touching any other field (PRD Section 91).

    Deactivating is the recommended way to "remove" an item from the menu:
    a historical order referencing it (Section 68) stores its own
    price/name snapshot, so it's unaffected either way, but deactivating
    -- unlike delete_menu_item -- keeps the item itself around to review
    or re-activate later.
    """
    db = get_db()
    result = db.menu.update_one(
        {"item_id": item_id},
        {"$set": {"active": bool(active), "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise AppError("item_not_found", f"No menu item found with id '{item_id}'.", 404)


def delete_menu_item(item_id: str) -> None:
    """Hard-delete a menu item (PRD Section 91).

    Prefer set_menu_item_active(item_id, False) when the item may be
    referenced by past orders -- this permanently removes the document.
    """
    db = get_db()
    result = db.menu.delete_one({"item_id": item_id})
    if result.deleted_count == 0:
        raise AppError("item_not_found", f"No menu item found with id '{item_id}'.", 404)
