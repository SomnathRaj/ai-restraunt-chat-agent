"""Admin login/logout (PRD Section 90)."""

from flask import redirect, render_template, request, session, url_for

from app.admin import bp
from app.extensions import limiter
from app.services import user_service

_GENERIC_ERROR = "Invalid email or password."


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if session.get("admin_user_id"):
        return redirect(url_for("admin.dashboard"))

    if request.method == "GET":
        return render_template("admin/login.html", error=None)

    email = request.form.get("email", "")
    password = request.form.get("password", "")
    user = user_service.authenticate(email, password)

    if user is None:
        return render_template("admin/login.html", error=_GENERIC_ERROR), 401

    session.clear()
    session["admin_user_id"] = user["user_id"]
    return redirect(url_for("admin.dashboard"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("admin_user_id", None)
    return redirect(url_for("admin.login"))
