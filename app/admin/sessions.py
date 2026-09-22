"""Admin chat session management (PRD Section 93).

Read-only + delete only -- there's no "edit a session" concept. The admin
either reviews a customer's conversation history or removes it entirely;
either way, app/services/session_service.py is the only thing that ever
touches the chat_sessions collection.
"""

from flask import redirect, render_template, request, url_for

from app.admin import bp
from app.services import session_service


@bp.get("/sessions")
def sessions_list():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    result = session_service.list_sessions_page(query, page)
    return render_template("admin/sessions_list.html", query=query, **result)


@bp.get("/sessions/<session_id>")
def session_detail(session_id):
    session = session_service.get_session_history(session_id)
    return render_template("admin/session_detail.html", session=session)


@bp.post("/sessions/<session_id>/delete")
def session_delete(session_id):
    session_service.delete_session(session_id)
    return redirect(url_for("admin.sessions_list"))
