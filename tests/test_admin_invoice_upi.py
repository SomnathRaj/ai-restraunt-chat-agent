"""UPI payment QR code on the printable invoice -- isolated from
test_admin_orders.py because it needs its own Config with UPI_ID set,
distinct from the shared TestConfig fixture in conftest.py (which
deliberately leaves UPI_ID unset, to cover the "not configured" path).
"""

import mongomock
import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import limiter as _limiter
from app.models.db import get_db
from app.services import cart_service, order_service, session_service
from config import Config

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"


class _UpiConfig(Config):
    MONGODB_URI = "mongodb://localhost/test"
    GEMINI_API_KEY = None
    WTF_CSRF_ENABLED = False
    UPI_ID = "restaurant@upi"


@pytest.fixture
def upi_app():
    flask_app = create_app(_UpiConfig)
    with flask_app.app_context():
        flask_app.extensions["mongo_client"] = mongomock.MongoClient()
        _limiter.reset()
        yield flask_app


@pytest.fixture
def upi_client(upi_app):
    return upi_app.test_client()


@pytest.fixture
def upi_admin_client(upi_app, upi_client):
    with upi_app.app_context():
        db = get_db()
        db.users.insert_one(
            {
                "user_id": "usr_test",
                "email": ADMIN_EMAIL,
                "password_hash": generate_password_hash(ADMIN_PASSWORD),
                "name": "Admin",
                "role": "admin",
                "active": True,
            }
        )
    upi_client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return upi_client


@pytest.fixture
def seeded_menu(upi_app):
    with upi_app.app_context():
        db = get_db()
        db.menu.insert_one(
            {
                "item_id": "chicken-biryani",
                "name": "Chicken Biryani",
                "description": "Spiced rice with chicken",
                "category": "Main Course",
                "price": 280,
                "availability": True,
                "is_veg": False,
                "tags": [],
                "active": True,
            }
        )
    return upi_app


def test_invoice_shows_upi_qr_code_when_configured(upi_admin_client, upi_app, seeded_menu):
    with upi_app.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        session_service.mark_instructions_prompted("s1")
        order = order_service.create_order("s1", "Somnath", "9876543210")

    response = upi_admin_client.get(f"/admin/orders/{order['order_id']}/invoice")
    assert response.status_code == 200
    body = response.data.decode()
    assert "data:image/png;base64," in body
    assert "Scan to pay via UPI" in body
    assert "restaurant@upi" in body


def test_invoice_qr_appears_before_thank_you_note(upi_admin_client, upi_app, seeded_menu):
    with upi_app.app_context():
        cart_service.add_to_cart("s1", "chicken-biryani", 1)
        session_service.mark_instructions_prompted("s1")
        order = order_service.create_order("s1", "Somnath", "9876543210")

    response = upi_admin_client.get(f"/admin/orders/{order['order_id']}/invoice")
    body = response.data.decode()
    qr_pos = body.index("Scan to pay via UPI")
    thanks_pos = body.index("Thank you for dining with us!")
    assert qr_pos < thanks_pos
