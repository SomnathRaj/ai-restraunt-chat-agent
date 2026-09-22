"""Free-text input cleanup shared by chat messages, names, and cooking instructions.

This is a lightweight defense layer (PRD Section 62) -- the primary protection
against prompt injection is architectural (see ARCHITECTURE.md Section 8), not
keyword filtering. This module only strips unsafe characters and caps length.
"""

import re
import unicodedata

# Strip C0/C1 control characters except newline/tab, which chat text may
# legitimately contain.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(raw: str, max_length: int) -> str:
    if raw is None:
        return ""
    text = unicodedata.normalize("NFC", raw)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = text.strip()
    return text[:max_length]


def slugify(text: str, fallback: str = "item") -> str:
    """Lowercase, hyphenated identifier derived from free text.

    Used by the admin portal (Phase 11) to turn a menu item name or FAQ
    question into an item_id/faq_id -- callers append a numeric suffix
    themselves if the result collides with an existing document.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug or fallback


def parse_comma_list(raw) -> list[str]:
    """Comma-separated form input -> a clean list of non-empty strings.

    Shared by the admin portal's menu `tags` and FAQ `keywords` fields
    (Phase 11) -- both are "free-text tags typed into one input," so there
    is exactly one implementation of turning that into a list.
    """
    items = raw if isinstance(raw, list) else (raw or "").split(",")
    return [item.strip() for item in items if item.strip()]
