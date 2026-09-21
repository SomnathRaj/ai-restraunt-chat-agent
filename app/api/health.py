"""GET /healthz -- reports whether MongoDB and Gemini are configured/reachable.

Lets you verify configuration state at a glance while waiting on credentials
(ARCHITECTURE.md Section 7): both should read false until MONGODB_URI /
GEMINI_API_KEY are set in .env, with no code changes needed once they are.
"""

from flask import Blueprint, jsonify

from app.ai.gemini_client import is_configured as gemini_is_configured
from app.models.db import ping as mongo_ping

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz():
    return jsonify({"mongodb": mongo_ping(), "gemini": gemini_is_configured()})
