"""One-off script to create/update the V1 admin account (PRD Section 90).

Usage (once MONGODB_URI is set in .env):
    python scripts/seed_admin_user.py

Safe to re-run -- upserts by email, and re-hashes the password each time
so changing ADMIN_PASSWORD below and re-running rotates it.
"""

import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash

from app import create_app
from app.models.db import ensure_indexes, get_db

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "pass123"


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        ensure_indexes(db)

        now = datetime.now(timezone.utc)
        db.users.update_one(
            {"email": ADMIN_EMAIL},
            {
                "$set": {
                    "email": ADMIN_EMAIL,
                    "password_hash": generate_password_hash(ADMIN_PASSWORD),
                    "name": "Admin",
                    "role": "admin",
                    "active": True,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "user_id": "usr_" + secrets.token_hex(12),
                    "created_at": now,
                },
            },
            upsert=True,
        )

        print(f"Seeded admin user: {ADMIN_EMAIL}")


if __name__ == "__main__":
    main()
