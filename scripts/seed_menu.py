"""One-off script to load sample menu docs (PRD Section 13) into MongoDB.

Usage (once MONGODB_URI is set in .env):
    python scripts/seed_menu.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.models.db import ensure_indexes, get_db

SAMPLE_MENU = [
    # Starters
    {
        "item_id": "paneer-tikka",
        "name": "Paneer Tikka",
        "description": "Char-grilled marinated cottage cheese.",
        "category": "Starter",
        "price": 180,
        "availability": True,
        "is_veg": True,
        "image": None,
        "tags": ["vegetarian", "starter", "spicy"],
        "active": True,
    },
    {
        "item_id": "chicken-tikka",
        "name": "Chicken Tikka",
        "description": "Char-grilled marinated chicken skewers.",
        "category": "Starter",
        "price": 220,
        "availability": True,
        "is_veg": False,
        "image": None,
        "tags": ["chicken", "starter", "spicy"],
        "active": True,
    },
    # Main Course
    {
        "item_id": "chicken-biryani",
        "name": "Chicken Biryani",
        "description": "Fragrant basmati rice layered with spiced chicken.",
        "category": "Main Course",
        "price": 280,
        "availability": True,
        "is_veg": False,
        "image": None,
        "tags": ["chicken", "rice", "spicy"],
        "active": True,
    },
    {
        "item_id": "veg-biryani",
        "name": "Veg Biryani",
        "description": "Fragrant basmati rice layered with mixed vegetables.",
        "category": "Main Course",
        "price": 220,
        "availability": True,
        "is_veg": True,
        "image": None,
        "tags": ["vegetarian", "rice"],
        "active": True,
    },
    # Burgers
    {
        "item_id": "veg-burger",
        "name": "Veg Burger",
        "description": "Classic vegetable patty burger.",
        "category": "Burger",
        "price": 150,
        "availability": True,
        "is_veg": True,
        "image": None,
        "tags": ["vegetarian", "burger"],
        "active": True,
    },
    {
        "item_id": "chicken-burger",
        "name": "Chicken Burger",
        "description": "Grilled chicken patty burger.",
        "category": "Burger",
        "price": 220,
        "availability": False,
        "is_veg": False,
        "image": None,
        "tags": ["chicken", "burger"],
        "active": True,
    },
    # Wraps -- alternatives for the unavailable Chicken Burger (PRD Section 18 example)
    {
        "item_id": "chicken-wrap",
        "name": "Chicken Wrap",
        "description": "Grilled chicken and salad rolled in a soft tortilla.",
        "category": "Wrap",
        "price": 190,
        "availability": True,
        "is_veg": False,
        "image": None,
        "tags": ["chicken", "wrap"],
        "active": True,
    },
    {
        "item_id": "chicken-roll",
        "name": "Chicken Roll",
        "description": "Spiced chicken and onions rolled in a paratha.",
        "category": "Wrap",
        "price": 170,
        "availability": True,
        "is_veg": False,
        "image": None,
        "tags": ["chicken", "wrap", "spicy"],
        "active": True,
    },
    # Beverages
    {
        "item_id": "coke",
        "name": "Coke",
        "description": "Chilled soft drink.",
        "category": "Beverage",
        "price": 60,
        "availability": True,
        "is_veg": True,
        "image": None,
        "tags": ["drink"],
        "active": True,
    },
    {
        "item_id": "fresh-lime",
        "name": "Fresh Lime",
        "description": "Freshly squeezed lime soda.",
        "category": "Beverage",
        "price": 80,
        "availability": True,
        "is_veg": True,
        "image": None,
        "tags": ["drink"],
        "active": True,
    },
    # Dessert
    {
        "item_id": "gulab-jamun",
        "name": "Gulab Jamun",
        "description": "Warm milk-solid dumplings soaked in sugar syrup.",
        "category": "Dessert",
        "price": 90,
        "availability": True,
        "is_veg": True,
        "image": None,
        "tags": ["dessert", "sweet"],
        "active": True,
    },
]


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        ensure_indexes(db)

        now = datetime.now(timezone.utc)
        for item in SAMPLE_MENU:
            db.menu.update_one(
                {"item_id": item["item_id"]},
                {"$set": {**item, "updated_at": now}, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )

        print(f"Seeded {len(SAMPLE_MENU)} menu items.")


if __name__ == "__main__":
    main()
