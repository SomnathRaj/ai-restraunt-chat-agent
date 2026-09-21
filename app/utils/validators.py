"""Customer-info validation (PRD Sections 29-30).

Raises AppError on invalid input so callers can let it propagate straight
to the standard JSON error response (see app/utils/errors.py).
"""

import re

from app.utils.errors import AppError

_NAME_MIN_LENGTH = 1
_NAME_MAX_LENGTH = 80

# V1 default per PRD Section 30: Indian 10-digit mobile numbers, e.g. 9876543210.
_INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def validate_customer_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    if not (_NAME_MIN_LENGTH <= len(name) <= _NAME_MAX_LENGTH):
        raise AppError("invalid_name", "Please provide a valid name.")
    return name


def validate_mobile_number(raw_mobile: str) -> str:
    mobile = re.sub(r"[\s-]", "", raw_mobile or "")
    if not _INDIAN_MOBILE_RE.match(mobile):
        raise AppError("invalid_mobile", "Please provide a valid mobile number.")
    return mobile
