"""One-off script to backfill 10 demo orders spread across the last 7 days.

Usage (once MONGODB_URI is set in .env):
    python scripts/seed_demo_orders.py

Not idempotent -- each run inserts 10 more orders. Order IDs are date-scoped
(ORD-YYYYMMDD-NNNN) and generated for their *backdated* created_at date, via
the same per-day counter order_service.generate_order_id() uses for real
orders, so they look indistinguishable from genuine historical orders and
never collide with same-day real traffic.
"""

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.models.db import get_db
from app.services import menu_service, order_service

CUSTOMERS = [
    ("Ravi Kumar", "9812345601"),
    ("Priya Singh", "9812345602"),
    ("Amit Sharma", "9812345603"),
    ("Sneha Rao", "9812345604"),
    ("Vikram Das", "9812345605"),
    ("Anjali Nair", "9812345606"),
    ("Rahul Verma", "9812345607"),
    ("Pooja Iyer", "9812345608"),
    ("Karan Mehta", "9812345609"),
    ("Divya Menon", "9812345610"),
]

STATUS_CHOICES = ["COMPLETED", "COMPLETED", "COMPLETED", "PROCESSING_FOOD", "CANCEL"]
PAYMENT_CHOICES = ["PAID", "PAID", "PAID", "DUE"]


def _random_timestamp_on(day_offset: int) -> datetime:
    """A random time of day, `day_offset` days before today (0 = today), in UTC."""
    base = datetime.now(timezone.utc) - timedelta(days=day_offset)
    return base.replace(hour=random.randint(11, 21), minute=random.randint(0, 59), second=random.randint(0, 59), microsecond=0)


def _order_id_for(created_at: datetime) -> str:
    date_str = created_at.strftime("%Y%m%d")
    seq = order_service.next_order_sequence(date_str)
    return f"ORD-{date_str}-{seq:04d}"


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        menu_items = [item for item in menu_service.list_all_menu_items() if item.get("active") and item.get("availability")]
        if not menu_items:
            print("No active/available menu items found -- seed the menu first.")
            return

        inserted = []
        for i in range(10):
            day_offset = random.randint(0, 6)
            created_at = _random_timestamp_on(day_offset)
            customer_name, mobile = CUSTOMERS[i]

            picks = random.sample(menu_items, k=random.randint(2, 4))
            items = [{"item_id": m["item_id"], "quantity": random.randint(1, 3), "special_instructions": None} for m in picks]
            order_items = order_service._resolve_order_items(items)
            subtotal = sum(line["total"] for line in order_items)

            order_id = _order_id_for(created_at)
            status = random.choice(STATUS_CHOICES)
            payment_status = "DUE" if status == "CANCEL" else random.choice(PAYMENT_CHOICES)

            order_doc = {
                "order_id": order_id,
                "customer_name": customer_name,
                "mobile": mobile,
                "items": order_items,
                "order_notes": None,
                "subtotal": subtotal,
                "total": subtotal,
                "status": status,
                "payment_status": payment_status,
                "created_by": "admin",
                "created_at": created_at,
                "updated_at": created_at,
            }
            db.orders.insert_one(order_doc)
            inserted.append((order_id, created_at.strftime("%Y-%m-%d %H:%M"), len(order_items), status))

        print(f"Seeded {len(inserted)} demo orders:")
        for order_id, when, item_count, status in inserted:
            print(f"  {order_id}  {when}  {item_count} items  {status}")


if __name__ == "__main__":
    main()
