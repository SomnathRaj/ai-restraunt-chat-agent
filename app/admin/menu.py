"""Admin menu management (PRD Section 91).

Every mutation goes through app/services/menu_service.py -- the same
service the customer-facing /api/menu routes and the AI's menu tools use
-- so a menu edit here is immediately visible everywhere else (there is no
cache to invalidate, ARCHITECTURE.md Section 12).
"""

from flask import redirect, render_template, request, url_for

from app.admin import bp
from app.services import menu_service
from app.utils.errors import AppError


def _form_kwargs():
    return {
        "name": request.form.get("name", ""),
        "description": request.form.get("description", ""),
        "category": request.form.get("category", ""),
        "price": request.form.get("price", ""),
        "availability": "availability" in request.form,
        "is_veg": "is_veg" in request.form,
        "tags": request.form.get("tags", ""),
        "image": request.form.get("image", "").strip() or None,
    }


@bp.get("/menu")
def menu_list():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    result = menu_service.list_menu_items_page(query, page)
    return render_template("admin/menu_list.html", query=query, **result)


@bp.route("/menu/new", methods=["GET", "POST"])
def menu_new():
    if request.method == "GET":
        return render_template(
            "admin/menu_form.html", item=None, error=None, categories=menu_service.list_categories()
        )

    kwargs = _form_kwargs()
    try:
        menu_service.create_menu_item(**kwargs)
    except AppError as err:
        return (
            render_template(
                "admin/menu_form.html", item=kwargs, error=err.message, categories=menu_service.list_categories()
            ),
            400,
        )

    return redirect(url_for("admin.menu_list"))


@bp.route("/menu/<item_id>/edit", methods=["GET", "POST"])
def menu_edit(item_id):
    if request.method == "GET":
        item = menu_service.get_menu_item_for_admin(item_id)
        return render_template(
            "admin/menu_form.html", item=item, error=None, categories=menu_service.list_categories()
        )

    kwargs = _form_kwargs()
    try:
        menu_service.update_menu_item(item_id, **kwargs)
    except AppError as err:
        item = {**kwargs, "item_id": item_id}
        return (
            render_template(
                "admin/menu_form.html", item=item, error=err.message, categories=menu_service.list_categories()
            ),
            400,
        )

    return redirect(url_for("admin.menu_list"))


@bp.post("/menu/<item_id>/toggle-active")
def menu_toggle_active(item_id):
    item = menu_service.get_menu_item_for_admin(item_id)
    menu_service.set_menu_item_active(item_id, not item["active"])
    return redirect(url_for("admin.menu_list"))


@bp.post("/menu/<item_id>/delete")
def menu_delete(item_id):
    menu_service.delete_menu_item(item_id)
    return redirect(url_for("admin.menu_list"))
