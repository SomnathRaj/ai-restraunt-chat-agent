"""Restaurant-level FAQ retrieval (PRD Section 19).

Called from both app/api/faq.py (REST) and app/ai/tool_executor.py (Gemini
tool calls) -- never re-implement this logic in either caller.

Matching is deliberately simple (token overlap against `question` +
`keywords`, active entries only) and happens entirely in Flask -- Gemini
never composes or guesses an answer itself (see ARCHITECTURE.md Section 6).
"""

import re
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.models.db import get_db
from app.utils.errors import AppError
from app.utils.pagination import paginate
from app.utils.sanitization import parse_comma_list, slugify

_ADMIN_PROJECTION = {"_id": 0}

_STOPWORDS = {
    "a", "an", "the", "is", "are", "do", "does", "you", "your", "we", "our",
    "i", "my", "to", "for", "of", "on", "in", "at", "please", "can", "could",
    "would", "will", "have", "has", "and", "or", "it", "this", "that",
}


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"\w+", (text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def search_faq(query: str) -> dict:
    """Return the best-matching FAQ answer, or an explicit no-match signal.

    Never fabricates an answer: {"matched": False} means the backend found
    nothing, and the system prompt instructs Gemini to say so honestly
    rather than guessing (PRD Section 19).
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return {"matched": False}

    db = get_db()
    best_doc = None
    best_score = 0

    for doc in db.faq.find({"active": True}):
        haystack = _tokenize(doc.get("question", ""))
        for keyword in doc.get("keywords") or []:
            haystack |= _tokenize(keyword)

        score = len(query_tokens & haystack)
        if score > best_score:
            best_score = score
            best_doc = doc

    if best_doc is None:
        return {"matched": False}

    return {
        "matched": True,
        "faq_id": best_doc.get("faq_id"),
        "question": best_doc.get("question"),
        "answer": best_doc.get("answer"),
        "category": best_doc.get("category"),
    }


# ---------------------------------------------------------------------------
# Admin portal (PRD Section 91) -- called only from app/admin/faq.py.
# ---------------------------------------------------------------------------


def list_all_faq_entries() -> list[dict]:
    """Return every FAQ entry, including inactive ones, unpaginated.

    For the admin's own /admin/faq list view, use list_faq_entries_page()
    below instead -- this is the full-access primitive other callers can
    build on.
    """
    db = get_db()
    cursor = db.faq.find({}, _ADMIN_PROJECTION)
    return list(cursor.sort([("category", 1), ("question", 1)]))


def list_faq_entries_page(query: str = "", page: int = 1, page_size: int = 20) -> dict:
    """Return one page of admin FAQ entries, optionally text-filtered.

    Searches question/answer/category/keywords across ALL entries
    (active or not).
    """
    db = get_db()
    query = (query or "").strip()
    mongo_filter = {}
    if query:
        pattern = re.escape(query)
        mongo_filter = {
            "$or": [
                {"question": {"$regex": pattern, "$options": "i"}},
                {"answer": {"$regex": pattern, "$options": "i"}},
                {"category": {"$regex": pattern, "$options": "i"}},
                {"keywords": {"$regex": pattern, "$options": "i"}},
            ]
        }

    total_count = db.faq.count_documents(mongo_filter)
    meta = paginate(total_count, page, page_size)
    cursor = (
        db.faq.find(mongo_filter, _ADMIN_PROJECTION)
        .sort([("category", 1), ("question", 1)])
        .skip(meta["skip"])
        .limit(meta["page_size"])
    )
    return {**meta, "entries": list(cursor)}


def get_faq_entry_for_admin(faq_id: str) -> dict:
    """Return one FAQ entry regardless of active state. Raises AppError 404 if missing."""
    db = get_db()
    doc = db.faq.find_one({"faq_id": faq_id}, _ADMIN_PROJECTION)
    if doc is None:
        raise AppError("faq_not_found", f"No FAQ entry found with id '{faq_id}'.", 404)
    return doc


def _unique_faq_id(db, base_slug: str) -> str:
    candidate = base_slug
    suffix = 2
    while db.faq.find_one({"faq_id": candidate}, {"_id": 1}):
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _validate_required_fields(question: str, answer: str) -> tuple[str, str]:
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question:
        raise AppError("missing_question", "Question is required.", 400)
    if not answer:
        raise AppError("missing_answer", "Answer is required.", 400)
    return question, answer


def create_faq_entry(*, question: str, answer: str, category: str, keywords) -> dict:
    """Create a new FAQ entry (PRD Section 91). Raises AppError on invalid input.

    faq_id is derived from the question (matching the seed script's slug
    convention), not entered by the admin.
    """
    question, answer = _validate_required_fields(question, answer)

    db = get_db()
    faq_id = _unique_faq_id(db, slugify(question, fallback="faq"))
    now = datetime.now(timezone.utc)
    doc = {
        "faq_id": faq_id,
        "question": question,
        "answer": answer,
        "category": (category or "").strip(),
        "keywords": parse_comma_list(keywords),
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    db.faq.insert_one(doc)
    doc.pop("_id", None)
    return doc


def update_faq_entry(faq_id: str, *, question: str, answer: str, category: str, keywords) -> dict:
    """Update an existing FAQ entry's editable fields (PRD Section 91).

    Never touches faq_id/active/created_at.
    """
    question, answer = _validate_required_fields(question, answer)

    db = get_db()
    update = {
        "question": question,
        "answer": answer,
        "category": (category or "").strip(),
        "keywords": parse_comma_list(keywords),
        "updated_at": datetime.now(timezone.utc),
    }
    result = db.faq.find_one_and_update(
        {"faq_id": faq_id},
        {"$set": update},
        projection=_ADMIN_PROJECTION,
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise AppError("faq_not_found", f"No FAQ entry found with id '{faq_id}'.", 404)
    return result


def set_faq_entry_active(faq_id: str, active: bool) -> None:
    """Activate/deactivate an FAQ entry without touching any other field (PRD Section 91)."""
    db = get_db()
    result = db.faq.update_one(
        {"faq_id": faq_id},
        {"$set": {"active": bool(active), "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise AppError("faq_not_found", f"No FAQ entry found with id '{faq_id}'.", 404)


def delete_faq_entry(faq_id: str) -> None:
    """Hard-delete an FAQ entry (PRD Section 91)."""
    db = get_db()
    result = db.faq.delete_one({"faq_id": faq_id})
    if result.deleted_count == 0:
        raise AppError("faq_not_found", f"No FAQ entry found with id '{faq_id}'.", 404)
