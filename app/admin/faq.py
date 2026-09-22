"""Admin FAQ management (PRD Section 91).

Every mutation goes through app/services/faq_service.py -- the same
service search_faq() (customer + AI tool) reads from, so an edit here is
immediately visible everywhere else (ARCHITECTURE.md Section 12).
"""

from flask import redirect, render_template, request, url_for

from app.admin import bp
from app.services import faq_service
from app.utils.errors import AppError


def _form_kwargs():
    return {
        "question": request.form.get("question", ""),
        "answer": request.form.get("answer", ""),
        "category": request.form.get("category", ""),
        "keywords": request.form.get("keywords", ""),
    }


@bp.get("/faq")
def faq_list():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    result = faq_service.list_faq_entries_page(query, page)
    return render_template("admin/faq_list.html", query=query, **result)


@bp.route("/faq/new", methods=["GET", "POST"])
def faq_new():
    if request.method == "GET":
        return render_template("admin/faq_form.html", entry=None, error=None)

    kwargs = _form_kwargs()
    try:
        faq_service.create_faq_entry(**kwargs)
    except AppError as err:
        return render_template("admin/faq_form.html", entry=kwargs, error=err.message), 400

    return redirect(url_for("admin.faq_list"))


@bp.route("/faq/<faq_id>/edit", methods=["GET", "POST"])
def faq_edit(faq_id):
    if request.method == "GET":
        entry = faq_service.get_faq_entry_for_admin(faq_id)
        return render_template("admin/faq_form.html", entry=entry, error=None)

    kwargs = _form_kwargs()
    try:
        faq_service.update_faq_entry(faq_id, **kwargs)
    except AppError as err:
        entry = {**kwargs, "faq_id": faq_id}
        return render_template("admin/faq_form.html", entry=entry, error=err.message), 400

    return redirect(url_for("admin.faq_list"))


@bp.post("/faq/<faq_id>/toggle-active")
def faq_toggle_active(faq_id):
    entry = faq_service.get_faq_entry_for_admin(faq_id)
    faq_service.set_faq_entry_active(faq_id, not entry["active"])
    return redirect(url_for("admin.faq_list"))


@bp.post("/faq/<faq_id>/delete")
def faq_delete(faq_id):
    faq_service.delete_faq_entry(faq_id)
    return redirect(url_for("admin.faq_list"))
