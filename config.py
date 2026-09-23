import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    """App configuration, read entirely from environment variables.

    MONGODB_URI intentionally has no default pointing at real infrastructure
    -- the app must boot with it unset (see /healthz).

    No AI provider's API key or model is read from here: they are entered in
    the admin portal (AI Settings) and stored encrypted in MongoDB
    (MULTI_AI_PROVIDER_DESIGN.md Section 4).
    """

    MONGODB_URI = os.environ.get("MONGODB_URI") or None
    MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "restaurant_bot")
    # App-level Fernet key that encrypts the provider API keys stored in
    # MongoDB -- not itself a provider credential. No default: the app still
    # boots without it, but no API key can be saved or used until it is set
    # (MULTI_AI_PROVIDER_DESIGN.md Appendix A).
    AI_CREDENTIALS_ENCRYPTION_KEY = os.environ.get("AI_CREDENTIALS_ENCRYPTION_KEY") or None

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
