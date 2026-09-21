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
