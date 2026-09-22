from werkzeug.security import generate_password_hash

from app.models.db import get_db

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


def test_login_page_renders_when_logged_out(client):
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert b"Admin Login" in response.data


def test_login_succeeds_with_seeded_credentials_and_sets_session(app, client):
    _seed_admin(app)
    response = client.post(
        "/admin/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/"

    with client.session_transaction() as flask_session:
        assert flask_session["admin_user_id"] == "usr_test"


def test_login_fails_with_wrong_password_generic_error(app, client):
    _seed_admin(app)
    response = client.post(
        "/admin/login",
        data={"email": ADMIN_EMAIL, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert b"Invalid email or password." in response.data

    with client.session_transaction() as flask_session:
        assert "admin_user_id" not in flask_session


def test_login_fails_with_unknown_email_identical_generic_error(app, client):
    _seed_admin(app)
    response = client.post(
        "/admin/login",
        data={"email": "nobody@example.com", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 401
    assert b"Invalid email or password." in response.data


def test_unauthenticated_request_redirects_to_login(client):
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"


def test_logout_clears_session(app, client):
    _seed_admin(app)
    client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    response = client.post("/admin/logout")
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/login"

    with client.session_transaction() as flask_session:
        assert "admin_user_id" not in flask_session

    dashboard_response = client.get("/admin/")
    assert dashboard_response.status_code == 302
    assert dashboard_response.headers["Location"] == "/admin/login"


def test_authenticated_admin_can_reach_dashboard(app, client):
    _seed_admin(app)
    client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    response = client.get("/admin/")
    assert response.status_code == 200
    assert b"Total Orders" in response.data


def test_admin_session_does_not_affect_customer_facing_endpoints(app, client):
    _seed_admin(app)
    client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    response = client.get("/api/menu")
    assert response.status_code == 200
