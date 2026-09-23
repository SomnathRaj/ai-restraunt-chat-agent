import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    """App configuration, read entirely from environment variables.

    MONGODB_URI and GEMINI_API_KEY intentionally have no default pointing at
    real infrastructure -- the app must boot with both unset (see /healthz).
    """

    MONGODB_URI = os.environ.get("MONGODB_URI") or None
    MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "restaurant_bot")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or None
    # "-latest" alias so the app follows Google's current Flash release
    # automatically instead of pinning a version that will be deprecated.
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

    RESTAURANT_NAME = os.environ.get("RESTRAUNT_NAME", "Restaurant Name")
    # No default -- a fake/placeholder UPI ID printed on a real invoice would
    # be actively wrong, not just incomplete, so the invoice's QR code is
    # simply omitted (not rendered with a bogus value) until this is set.
    UPI_ID = os.environ.get("UPI_ID") or None

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or "dev-only-insecure-key"

    CORS_ALLOWED_ORIGINS = [
        origin.strip()
        for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5000").split(",")
        if origin.strip()
    ]

    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    PORT = int(os.environ.get("PORT", 5000))

    MAX_MESSAGE_LENGTH = 2000
    SESSION_TTL_SECONDS = 24 * 60 * 60
    HISTORY_MAX_MESSAGES = 20
    # Empirically tuned against the real Gemini API (2026-09-21, model
    # gemini-3.5-flash-lite): this model calls one tool per round-trip
    # rather than batching, and a single-item order with a cooking
    # instruction alone took 4 tool calls + 1 final text turn = 5 calls,
    # right at the old limit of 5. 8 gives real headroom for multi-item
    # orders without letting a genuinely stuck loop run indefinitely.
    TOOL_LOOP_MAX_ITERATIONS = 8

    MAX_ITEM_QUANTITY = 50
    INSTRUCTIONS_MAX_LENGTH = 200
    GSTINNO = os.environ.get("GSTINNO", "")
