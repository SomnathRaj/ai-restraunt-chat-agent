"""Restaurant-level FAQ retrieval (PRD Section 19).

Called from both app/api/faq.py (REST) and app/ai/tool_executor.py (Gemini
tool calls) -- never re-implement this logic in either caller.

Matching is deliberately simple (token overlap against `question` +
`keywords`, active entries only) and happens entirely in Flask -- Gemini
never composes or guesses an answer itself (see ARCHITECTURE.md Section 6).
"""

import re

from app.models.db import get_db

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
