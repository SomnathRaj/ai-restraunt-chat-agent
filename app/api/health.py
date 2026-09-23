"""GET /healthz -- reports whether MongoDB is reachable and an AI provider is ready.

"ai" is true once an admin has made a provider active in Admin -> AI Settings
and its stored API key can be decrypted (MULTI_AI_PROVIDER_DESIGN.md Section 4).
Both read false on a fresh install, with no code changes needed once set up.
"""

from flask import Blueprint, jsonify

from app.models.db import ping as mongo_ping
from app.services import ai_provider_service

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz():
    mongodb = mongo_ping()
    return jsonify({"mongodb": mongodb, "ai": mongodb and ai_provider_service.active_provider_ready()})
