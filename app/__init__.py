import logging

from flask import Flask, render_template

from app.extensions import cors, limiter
from app.utils.errors import register_error_handlers
from config import Config


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    logging.basicConfig(level=logging.INFO)

    cors.init_app(app, origins=app.config["CORS_ALLOWED_ORIGINS"])
    limiter.init_app(app)

    register_error_handlers(app)

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

    @app.get("/")
    def index():
        return render_template("index.html", restaurant_name=app.config["RESTAURANT_NAME"])

    return app
