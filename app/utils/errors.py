"""Standard JSON error responses + Flask error handler registration.

Every error the client sees has the shape {"error": "<code>", "message": "<text>"}
so the frontend/AI layer can branch on `error` without parsing prose.
"""

import logging

from flask import jsonify
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import HTTPException

from app.models.db import DatabaseNotConfigured

log = logging.getLogger(__name__)


class AppError(Exception):
    """Raised by services/routes for any expected, client-facing failure."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_response(code: str, message: str, status_code: int):
    return jsonify({"error": code, "message": message}), status_code


def register_error_handlers(app):
    @app.errorhandler(AppError)
    def handle_app_error(err: AppError):
        return error_response(err.code, err.message, err.status_code)

    @app.errorhandler(DatabaseNotConfigured)
    def handle_db_not_configured(err: DatabaseNotConfigured):
        return error_response(
            "database_not_configured",
            "MongoDB is not configured yet. Set MONGODB_URI in .env.",
            503,
        )

    @app.errorhandler(NotImplementedError)
    def handle_not_implemented(err: NotImplementedError):
        return error_response("not_implemented", str(err) or "Not implemented yet.", 501)

    @app.errorhandler(404)
    def handle_404(err):
        return error_response("not_found", "The requested resource was not found.", 404)

    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_exceeded(err: RateLimitExceeded):
        return error_response(
            "rate_limited", "You're sending requests too quickly. Please slow down and try again in a moment.", 429
        )

    # Safety net for every OTHER Werkzeug HTTPException (405 Method Not
    # Allowed, 400 malformed request, etc.) that doesn't have its own
    # handler above. Without this, the catch-all Exception handler below
    # would swallow them too -- Flask does not special-case HTTPException
    # once a broader Exception handler is registered, so a real 429/405
    # would otherwise come back as an opaque 500 "internal_error" (this
    # was caught by tests/test_chat_api.py's rate-limit test).
    @app.errorhandler(HTTPException)
    def handle_http_exception(err: HTTPException):
        return error_response(err.name.lower().replace(" ", "_"), err.description or str(err), err.code or 500)

    @app.errorhandler(Exception)
    def handle_unexpected(err: Exception):
        log.exception("Unhandled exception")
        return error_response("internal_error", "Something went wrong. Please try again.", 500)
