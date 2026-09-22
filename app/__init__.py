import logging
import os
from datetime import datetime, timezone

from flask import Flask, render_template

from app.extensions import cors, csrf, limiter
from app.utils.errors import register_error_handlers
from app.utils.number_to_words import amount_in_words
from config import Config


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # The admin portal's login is Flask's signed session cookie -- its only
    # real protection is SECRET_KEY. Config.py's fallback ("dev-only-insecure-key")
    # is committed to this repo, so it must never silently become the actual
    # signing key in production (that would let anyone forge an admin session).
    # Gated on FLASK_ENV rather than always-on so local dev/tests, which never
    # set FLASK_SECRET_KEY, keep booting without it.
    if app.config["FLASK_ENV"] == "production" and not os.environ.get("FLASK_SECRET_KEY"):
        raise RuntimeError(
            "FLASK_SECRET_KEY must be set when FLASK_ENV=production -- refusing "
            "to start with the public, committed dev fallback as the real "
            "admin-session signing key."
        )

    logging.basicConfig(level=logging.INFO)

    cors.init_app(app, origins=app.config["CORS_ALLOWED_ORIGINS"])
    limiter.init_app(app)
    csrf.init_app(app)

    register_error_handlers(app)

    from app.admin import bp as admin_bp
    from app.api.cart import bp as cart_bp
    from app.api.chat import bp as chat_bp
    from app.api.faq import bp as faq_bp
    from app.api.health import bp as health_bp
    from app.api.menu import bp as menu_bp
    from app.api.orders import bp as orders_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(menu_bp)
    app.register_blueprint(faq_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(admin_bp)

    # These are called via fetch() with a JSON body, never a server-rendered
    # form -- CSRFProtect's default (every POST/PUT/PATCH/DELETE needs a
    # csrf_token) would otherwise break all of them. Only the admin blueprint
    # (server-rendered forms) keeps CSRF protection (ARCHITECTURE.md Section 12).
    csrf.exempt(health_bp)
    csrf.exempt(menu_bp)
    csrf.exempt(faq_bp)
    csrf.exempt(cart_bp)
    csrf.exempt(orders_bp)
    csrf.exempt(chat_bp)

    @app.get("/")
    def index():
        return render_template("index.html", restaurant_name=app.config["RESTAURANT_NAME"])

    @app.template_filter("admin_dt")
    def format_admin_datetime(value):
        """Compact timestamp for admin templates -- drops the seconds/microseconds
        noise a raw MongoDB datetime would otherwise print as (Phase 14)."""
        if value is None:
            return "—"
        return value.strftime("%Y-%m-%d %H:%M")

    app.add_template_filter(amount_in_words, "amount_in_words")

    @app.context_processor
    def inject_current_year():
        return {"current_year": datetime.now(timezone.utc).year}

    return app
