"""One-off script to load starter FAQ docs (PRD Section 19) into MongoDB.

Usage (once MONGODB_URI is set in .env):
    python scripts/seed_faq.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.models.db import ensure_indexes, get_db

SAMPLE_FAQ = [
    {
        "faq_id": "hours",
        "question": "What are your opening hours?",
        "answer": "We're open every day from 11 AM to 11 PM.",
        "category": "hours",
        "keywords": ["hours", "timing", "open", "close", "opening time", "closing time"],
        "active": True,
    },
    {
        "faq_id": "address",
        "question": "Where are you located?",
        "answer": "We're at 12 MG Road, near City Centre. Look for the red signboard.",
        "category": "location",
        "keywords": ["address", "location", "where", "directions"],
        "active": True,
    },
    {
        "faq_id": "delivery-time",
        "question": "How long does delivery take?",
        "answer": "Delivery usually takes 30-45 minutes depending on your location.",
        "category": "delivery",
        "keywords": ["delivery time", "how long", "eta", "delivery"],
        "active": True,
    },
    {
        "faq_id": "payment-methods",
        "question": "Do you accept cards?",
        "answer": "Yes, we accept both cash and card payments, as well as UPI.",
        "category": "payments",
        "keywords": ["payment", "card", "cash", "upi", "pay"],
        "active": True,
    },
    {
        "faq_id": "parking",
        "question": "Do you have parking?",
        "answer": "Yes! Free parking is available right outside the restaurant.",
        "category": "facilities",
        "keywords": ["parking", "car park", "park"],
        "active": True,
    },
    {
        "faq_id": "reservation",
        "question": "Can I make a reservation?",
        "answer": "Yes, please call us at least an hour ahead and we'll hold a table for you.",
        "category": "reservation",
        "keywords": ["reservation", "book a table", "booking", "reserve"],
        "active": True,
    },
    {
        "faq_id": "contact-number",
        "question": "What's your phone number?",
        "answer": "You can reach us at +91 98765 43210.",
        "category": "contact",
        "keywords": ["phone", "number", "contact", "call"],
        "active": True,
    },
    {
        "faq_id": "allergen-policy",
        "question": "Can you handle food allergies?",
        "answer": "Please let us know about any allergies when ordering and we'll do our best to accommodate them, though our kitchen handles common allergens like nuts and dairy.",
        "category": "dietary",
        "keywords": ["allergy", "allergen", "gluten", "nuts", "dairy"],
        "active": True,
    },
]


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        ensure_indexes(db)

        now = datetime.now(timezone.utc)
        for entry in SAMPLE_FAQ:
            db.faq.update_one(
                {"faq_id": entry["faq_id"]},
                {"$set": {**entry, "updated_at": now}, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )

        print(f"Seeded {len(SAMPLE_FAQ)} FAQ entries.")


if __name__ == "__main__":
    main()
