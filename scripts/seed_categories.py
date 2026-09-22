"""One-off script to load the predefined menu categories into MongoDB.

The admin menu form's Category field is a dropdown sourced from this
collection -- there's no admin UI to manage it (by design), just this
fixed reference list. Safe to re-run (upserts by name).

Usage (once MONGODB_URI is set in .env):
    python scripts/seed_categories.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.models.db import ensure_indexes, get_db

CATEGORIES = ["Starter", "Main Course", "Burger", "Wrap", "Beverage", "Dessert"]


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        ensure_indexes(db)

        for name in CATEGORIES:
            db.categories.update_one({"name": name}, {"$setOnInsert": {"name": name}}, upsert=True)

        print(f"Seeded {len(CATEGORIES)} categories.")


if __name__ == "__main__":
    main()
