"""Admin portal blueprint (PRD Section 89 onward).

Server-rendered Flask + Jinja pages under /admin/* -- a separate surface
from the customer chat, with no Gemini involvement (ARCHITECTURE.md
Section 12). Every route except /admin/login requires a valid admin
session; the before_request guard below makes that the default for every
route registered on this blueprint, rather than something each view has
to remember to check individually.
"""

from flask import Blueprint, redirect, request, session, url_for

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Endpoint names (not URLs) that don't require a logged-in admin session.
_PUBLIC_ENDPOINTS = {"admin.login"}


@bp.before_request
def require_admin_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if not session.get("admin_user_id"):
        return redirect(url_for("admin.login"))
    return None


# Imported for their route-registration side effect (each module does
# `from app.admin import bp` and defines routes via @bp.route(...)) -- import
# at the bottom to avoid a circular import with the modules that need `bp`.
from app.admin import auth, dashboard, faq, menu, orders, sessions  # noqa: E402,F401
