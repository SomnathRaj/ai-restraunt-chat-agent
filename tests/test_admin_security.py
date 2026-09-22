"""Admin portal security hardening (PRD Section 90, ARCHITECTURE.md Section 12).

Covers the things a functional CRUD test wouldn't catch: every admin route
actually enforces the login guard, CSRF is genuinely validated (not just
present in the HTML), the login rate limit actually trips, and the users
collection never leaks anywhere outside app/services/user_service.py.
"""

import re
from pathlib import Path

import mongomock
import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import limiter as _limiter
from app.models.db import get_db
from config import Config

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"


def _seed_admin(app):
    with app.app_context():
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


def _extract_csrf_token(html: bytes) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf_token field found in the rendered page"
    return match.group(1).decode()


# ---------------------------------------------------------------------------
# Every /admin/* route requires login (except /admin/login itself)
# ---------------------------------------------------------------------------

_ROUTE_APP = create_app(Config)


def _admin_routes_requiring_login():
    """Enumerate every registered admin route/method pair except the login
    page itself, straight from the app's own url_map -- so this test stays
    correct automatically as new admin routes are added, instead of relying
    on a hand-maintained list that can silently drift out of date."""
    seen = []
    for rule in _ROUTE_APP.url_map.iter_rules():
        if not rule.endpoint.startswith("admin.") or rule.endpoint == "admin.login":
            continue
        path = re.sub(r"<[^:>]+(?::[^>]+)?>", "dummy-id", rule.rule)
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            seen.append(pytest.param(method, path, id=f"{rule.endpoint}:{method}"))
    return seen


@pytest.mark.parametrize("method,path", _admin_routes_requiring_login())
def test_every_admin_route_redirects_to_login_when_logged_out(client, method, path):
    response = client.open(path, method=method)
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"
    # A bare Werkzeug redirect page is tiny -- this is a cheap structural
    # check that no menu/order/session/customer data rode along with it.
    assert len(response.data) < 300


# ---------------------------------------------------------------------------
# CSRF actually validates the token, not just requires the field to exist
# ---------------------------------------------------------------------------


class _CSRFEnabledConfig(Config):
    MONGODB_URI = "mongodb://localhost/test"
    GEMINI_API_KEY = None
    WTF_CSRF_ENABLED = True


@pytest.fixture
def csrf_app():
    flask_app = create_app(_CSRFEnabledConfig)
    with flask_app.app_context():
        flask_app.extensions["mongo_client"] = mongomock.MongoClient()
        _limiter.reset()
        yield flask_app


@pytest.fixture
def csrf_client(csrf_app):
    return csrf_app.test_client()


def test_login_post_with_no_csrf_token_is_rejected(csrf_app, csrf_client):
    _seed_admin(csrf_app)
    csrf_client.get("/admin/login")  # establishes a real session

    response = csrf_client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    assert response.status_code == 400
    with csrf_client.session_transaction() as session:
        assert "admin_user_id" not in session


def test_login_post_with_mismatched_csrf_token_is_rejected(csrf_app, csrf_client):
    _seed_admin(csrf_app)
    csrf_client.get("/admin/login")  # seeds a real CSRF secret into the session

    response = csrf_client.post(
        "/admin/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "csrf_token": "not-the-real-token"},
    )

    assert response.status_code == 400
    with csrf_client.session_transaction() as session:
        assert "admin_user_id" not in session


def test_login_post_with_valid_csrf_token_succeeds(csrf_app, csrf_client):
    """Positive control -- proves the above two tests fail for the *right*
    reason (a bad token), not because CSRF blocks every request outright."""
    _seed_admin(csrf_app)
    login_page = csrf_client.get("/admin/login")
    token = _extract_csrf_token(login_page.data)

    response = csrf_client.post(
        "/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "csrf_token": token}
    )

    assert response.status_code == 302
    with csrf_client.session_transaction() as session:
        assert session["admin_user_id"] == "usr_test"


def test_delete_action_with_no_csrf_token_is_rejected(csrf_app, csrf_client):
    """Same guarantee on a real mutating admin action, not just login."""
    _seed_admin(csrf_app)
    login_page = csrf_client.get("/admin/login")
    token = _extract_csrf_token(login_page.data)
    csrf_client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "csrf_token": token})

    with csrf_app.app_context():
        db = get_db()
        db.menu.insert_one(
            {
                "item_id": "coke",
                "name": "Coke",
                "description": "",
                "category": "Beverage",
                "price": 60,
                "availability": True,
                "is_veg": True,
                "tags": [],
                "active": True,
            }
        )

    response = csrf_client.post("/admin/menu/coke/delete", data={})

    assert response.status_code == 400
    with csrf_app.app_context():
        db = get_db()
        assert db.menu.find_one({"item_id": "coke"}) is not None


# ---------------------------------------------------------------------------
# /admin/login rate limiting actually trips
# ---------------------------------------------------------------------------


def test_admin_login_rate_limit_trips_after_repeated_failures(app, client):
    _seed_admin(app)

    statuses = []
    for _ in range(6):
        response = client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": "wrong-password"})
        statuses.append(response.status_code)

    assert statuses[:5] == [401, 401, 401, 401, 401]
    assert statuses[5] == 429


def test_admin_login_rate_limit_is_scoped_per_ip_not_a_global_lockout(app, client):
    """Flask-Limiter here keys by remote IP (app/extensions.py's `get_remote_address`),
    not by session/cookie -- so a client that simply clears cookies must NOT
    escape the block (that would make the whole guard trivially bypassable).
    What must stay unaffected is a *different* IP: this proves the trip is a
    real per-IP limit and not, say, a bug that locks out the whole app."""
    _seed_admin(app)
    for _ in range(5):
        client.post(
            "/admin/login",
            data={"email": ADMIN_EMAIL, "password": "wrong-password"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.1"},
        )
    blocked = client.post(
        "/admin/login",
        data={"email": ADMIN_EMAIL, "password": "wrong-password"},
        environ_overrides={"REMOTE_ADDR": "10.0.0.1"},
    )
    assert blocked.status_code == 429

    response = client.post(
        "/admin/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        environ_overrides={"REMOTE_ADDR": "10.0.0.2"},
    )
    assert response.status_code == 302


# ---------------------------------------------------------------------------
# users collection / admin credentials never leak
# ---------------------------------------------------------------------------


def test_users_collection_only_touched_by_user_service_and_db_setup():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    allowed = {app_dir / "services" / "user_service.py", app_dir / "models" / "db.py"}

    offenders = [
        path
        for path in app_dir.rglob("*.py")
        if path not in allowed and re.search(r"\bdb\.users\b", path.read_text())
    ]
    assert offenders == []


def test_customer_facing_endpoints_never_expose_admin_credentials(client, app):
    with app.app_context():
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
        db.menu.insert_one(
            {
                "item_id": "coke",
                "name": "Coke",
                "description": "Chilled soft drink",
                "category": "Beverage",
                "price": 60,
                "availability": True,
                "is_veg": True,
                "tags": ["drink"],
                "active": True,
            }
        )
        db.faq.insert_one(
            {
                "faq_id": "hours",
                "question": "What are your opening hours?",
                "answer": "11 AM to 11 PM.",
                "category": "hours",
                "keywords": ["hours"],
                "active": True,
            }
        )

    for path in ("/healthz", "/api/menu", "/api/menu/search?q=coke", "/api/faq/search?q=hours"):
        response = client.get(path)
        assert ADMIN_PASSWORD.encode() not in response.data
        assert b"password_hash" not in response.data
        assert ADMIN_EMAIL.encode() not in response.data


@pytest.fixture
def admin_logged_in_client(app, client):
    _seed_admin(app)
    client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return client


def test_admin_pages_never_render_the_password_or_hash(admin_logged_in_client):
    response = admin_logged_in_client.get("/admin/")
    assert ADMIN_PASSWORD.encode() not in response.data
    assert b"password_hash" not in response.data
