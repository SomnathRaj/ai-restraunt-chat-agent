"""Admin dashboard landing page (PRD Section 89).

Quick-review stats and a sales graph, filterable by date range (today /
this week / a custom range). All figures come from
app/services/dashboard_service.py -- this view is a thin read-only layer
over it, same as every other admin page over its service module.
"""

from flask import render_template, request

from app.admin import bp
from app.services import dashboard_service


@bp.get("/")
def dashboard():
    range_key, start, end = dashboard_service.resolve_date_range(
        request.args.get("range", "today"),
        request.args.get("start"),
        request.args.get("end"),
    )
    order_stats = dashboard_service.get_order_analytics(start, end)

    return render_template(
        "admin/dashboard.html",
        range_key=range_key,
        range_label=dashboard_service.format_range_label(range_key, start, end),
        start_input=request.args.get("start", ""),
        end_input=request.args.get("end", ""),
        order_stats=order_stats,
        customer_count=dashboard_service.get_customer_count(start, end),
        availability_counts=dashboard_service.get_menu_availability_counts(),
        diet_counts=dashboard_service.get_veg_nonveg_counts(),
    )
