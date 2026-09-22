"""Admin authentication (PRD Section 90).

Called only from app/admin/auth.py -- there is no customer-facing or AI
tool-call path into this service, unlike every other service in this
package (admin login is not a conversation).
"""

from werkzeug.security import check_password_hash

from app.models.db import get_db


def authenticate(email: str, password: str) -> dict | None:
    """Return the matching active admin user doc, or None if invalid.

    Deliberately returns the same None (and the caller shows the same
    generic message) whether the email doesn't exist or the password is
    wrong -- never reveal which part was incorrect (PRD Section 90).
    """
    email = (email or "").strip().lower()
    if not email or not password:
        return None

    db = get_db()
    user = db.users.find_one({"email": email, "active": True})
    if user is None:
        return None

    if not check_password_hash(user["password_hash"], password):
        return None

    return user
