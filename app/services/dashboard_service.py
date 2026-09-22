"""Admin dashboard analytics -- quick-review order stats, a sales graph,
customer/menu snapshots (PRD Section 89). Read-only aggregate queries over
orders/chat_sessions/menu; called only from app/admin/dashboard.py.

Plain Python grouping over db.find(...) results, not a Mongo aggregation
pipeline -- order volumes for a single restaurant are small, and this stays
simple and identically testable against both mongomock and real MongoDB.
"""

from datetime import datetime, timedelta, timezone

from app.models.db import get_db

_ORDER_STATUSES = ("PENDING", "PROCESSING_FOOD", "COMPLETED", "CANCEL")


def resolve_date_range(range_key: str, start_str: str | None, end_str: str | None) -> tuple[str, datetime, datetime]:
    """Turn a range key (today/week/custom) + optional custom bounds into
    concrete [start, end) UTC datetimes.

    Falls back to "today" if "custom" is requested without two valid,
    correctly-ordered dates -- the dashboard should never 500 on a bad
    query string, just show something sensible.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if range_key == "week":
        week_start = today_start - timedelta(days=today_start.weekday())
        return "week", week_start, week_start + timedelta(days=7)

    if range_key == "custom" and start_str and end_str:
        try:
            start = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            start = end = None
        if start is not None and end > start:
            return "custom", start, end

    return "today", today_start, today_start + timedelta(days=1)


def format_range_label(range_key: str, start: datetime, end: datetime) -> str:
    """Human-readable label for the dashboard's active filter, e.g.
    "This week (2026-09-21 to 2026-09-27)"."""
    start_str = start.strftime("%Y-%m-%d")
    inclusive_end_str = (end - timedelta(days=1)).strftime("%Y-%m-%d")
    if range_key == "today":
        return f"Today ({start_str})"
    if range_key == "week":
        return f"This week ({start_str} to {inclusive_end_str})"
    return f"Custom range ({start_str} to {inclusive_end_str})"


def get_order_analytics(start: datetime, end: datetime) -> dict:
    """Return status counts, a zero-filled daily sales series, and totals
    for orders created in [start, end)."""
    db = get_db()
    docs = list(
        db.orders.find({"created_at": {"$gte": start, "$lt": end}}, {"_id": 0, "status": 1, "total": 1, "created_at": 1})
    )

    status_counts = {status: 0 for status in _ORDER_STATUSES}
    daily_totals: dict[str, float] = {}
    total_sales = 0
    for doc in docs:
        status = doc.get("status")
        if status in status_counts:
            status_counts[status] += 1
        day_key = doc["created_at"].strftime("%Y-%m-%d")
        daily_totals[day_key] = daily_totals.get(day_key, 0) + doc["total"]
        total_sales += doc["total"]

    daily_sales = []
    day = start
    while day < end:
        key = day.strftime("%Y-%m-%d")
        daily_sales.append({"date": key, "total": daily_totals.get(key, 0)})
        day += timedelta(days=1)

    return {
        "status_counts": status_counts,
        "total_orders": len(docs),
        "total_sales": total_sales,
        "daily_sales": daily_sales,
    }


def get_customer_count(start: datetime, end: datetime) -> int:
    """Count of chat sessions started in [start, end).

    A session isn't a verified identity (no customer accounts, PRD Section
    42), but it's the closest signal this app has for "someone engaged with
    the chat" in the period.
    """
    db = get_db()
    return db.chat_sessions.count_documents({"created_at": {"$gte": start, "$lt": end}})


def get_veg_nonveg_counts() -> dict:
    """Current (not date-filtered) counts of active menu items by diet type."""
    db = get_db()
    return {
        "veg": db.menu.count_documents({"active": True, "is_veg": True}),
        "nonveg": db.menu.count_documents({"active": True, "is_veg": False}),
    }


def get_ordered_veg_nonveg_counts(start: datetime, end: datetime) -> dict:
    """Veg/non-veg split of item *quantities actually ordered* in [start, end)
    -- unlike get_veg_nonveg_counts() above (the menu's current composition),
    this reflects real customer demand for the selected range, matching the
    same [start, end) window as get_order_analytics() (all statuses, same
    as "Total Orders"/"Total Sales" above it).

    Orders don't snapshot is_veg on their own item lines (only name/price/
    quantity), so each line's diet type is looked up from the *current*
    menu doc by item_id. An item since deleted from the menu can't be
    classified and is silently excluded -- inventing a bucket for it would
    be misleading, and this is a lightweight dashboard summary, not an
    audit trail.
    """
    db = get_db()
    orders = list(db.orders.find({"created_at": {"$gte": start, "$lt": end}}, {"_id": 0, "items": 1}))

    item_ids = {line["item_id"] for order in orders for line in order.get("items", [])}
    is_veg_by_item_id = {
        doc["item_id"]: doc["is_veg"]
        for doc in db.menu.find({"item_id": {"$in": list(item_ids)}}, {"_id": 0, "item_id": 1, "is_veg": 1})
    }

    veg = nonveg = 0
    for order in orders:
        for line in order.get("items", []):
            is_veg = is_veg_by_item_id.get(line["item_id"])
            if is_veg is True:
                veg += line["quantity"]
            elif is_veg is False:
                nonveg += line["quantity"]

    return {"veg": veg, "nonveg": nonveg}
