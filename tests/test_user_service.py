from werkzeug.security import generate_password_hash

from app.models.db import get_db
from app.services import user_service


def _seed_user(app, *, email="admin@gmail.com", password="pass123", active=True):
    with app.app_context():
        db = get_db()
        db.users.insert_one(
            {
                "user_id": "usr_test",
                "email": email,
                "password_hash": generate_password_hash(password),
                "name": "Admin",
                "role": "admin",
                "active": active,
            }
        )


def test_authenticate_returns_user_on_correct_credentials(app):
    _seed_user(app)
    with app.app_context():
        user = user_service.authenticate("admin@gmail.com", "pass123")
    assert user is not None
    assert user["email"] == "admin@gmail.com"


def test_authenticate_returns_none_on_wrong_password(app):
    _seed_user(app)
    with app.app_context():
        user = user_service.authenticate("admin@gmail.com", "wrong-password")
    assert user is None


def test_authenticate_returns_none_on_unknown_email(app):
    _seed_user(app)
    with app.app_context():
        user = user_service.authenticate("nobody@example.com", "pass123")
    assert user is None


def test_authenticate_returns_none_on_inactive_user(app):
    _seed_user(app, active=False)
    with app.app_context():
        user = user_service.authenticate("admin@gmail.com", "pass123")
    assert user is None


def test_authenticate_is_case_insensitive_on_email(app):
    _seed_user(app)
    with app.app_context():
        user = user_service.authenticate("ADMIN@GMAIL.com", "pass123")
    assert user is not None


def test_authenticate_returns_none_on_missing_fields(app):
    _seed_user(app)
    with app.app_context():
        assert user_service.authenticate("", "pass123") is None
        assert user_service.authenticate("admin@gmail.com", "") is None


def test_stored_password_is_never_plaintext(app):
    _seed_user(app)
    with app.app_context():
        db = get_db()
        user = db.users.find_one({"email": "admin@gmail.com"})
    assert user["password_hash"] != "pass123"
    assert "password" not in user
