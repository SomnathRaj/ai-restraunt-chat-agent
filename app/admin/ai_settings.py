"""Admin AI Settings (MULTI_AI_PROVIDER_DESIGN.md Section 5).

Every rule (fixed provider list, one active provider, encryption, "configured"
means key + model) lives in app/services/ai_provider_service.py -- this view
only orchestrates the page's flows:

- Save on an inactive provider saves, then runs a connection test on the
  saved values, so the result is known before anyone makes it active.
- Save on the ACTIVE provider tests the new values first and saves nothing
  if the test fails, unless the admin ticks "Save anyway" -- customers are
  never moved onto a broken key/model by accident (soft warning, not a block).
- Making a provider active that hasn't passed a test needs "Activate anyway".

A saved API key is never sent back to the browser: the key field is always
rendered empty, with a masked "saved on <date>" placeholder.
"""

from flask import flash, redirect, render_template, request, session, url_for

from app.admin import bp
from app.ai.providers import registry
from app.services import ai_provider_service
from app.utils.credentials_crypto import encryption_key_status
from app.utils.errors import AppError


def _admin_id() -> str:
    return session.get("admin_user_id", "")


def _render(status: int = 200, *, form_state: dict | None = None, activate_state: dict | None = None):
    return (
        render_template(
            "admin/ai_settings.html",
            providers=ai_provider_service.list_providers(),
            active=ai_provider_service.get_active_summary(),
            encryption_status=encryption_key_status(),
            form_state=form_state or {},
            activate_state=activate_state or {},
        ),
        status,
    )


def _back_to(provider: str | None = None):
    return redirect(url_for("admin.ai_settings") + (f"#provider-{provider}" if provider else ""))


@bp.get("/settings/ai")
def ai_settings():
    return _render()


@bp.post("/settings/ai/<provider>")
def ai_settings_save(provider):
    api_key = request.form.get("api_key", "")
    model = request.form.get("model", "")
    try:
        if request.form.get("action") == "test":
            return _test(provider, api_key, model)
        if ai_provider_service.get_provider(provider)["active"]:
            return _save_active(provider, api_key, model)
        return _save_inactive(provider, api_key, model)
    except AppError as err:
        return _render(err.status_code, form_state={provider: {"model": model, "error": err.message}})


def _test(provider, api_key, model):
    saved = ai_provider_service.get_provider(provider)
    key, test_model = ai_provider_service.credentials_for_test(provider, api_key=api_key, model=model)
    ok, message = registry.check_connection(provider, api_key=key, model=test_model)
    tested_saved_values = not api_key.strip() and test_model == saved["model"]
    if tested_saved_values:
        ai_provider_service.record_test_result(provider, ok)
    elif ok:
        message += " Nothing was saved yet: re-enter the key if you typed one, then click Save."
    flash(message, "success" if ok else "error")
    return _back_to(provider)


def _save_active(provider, api_key, model):
    key, test_model = ai_provider_service.credentials_for_test(provider, api_key=api_key, model=model)
    ok, message = registry.check_connection(provider, api_key=key, model=test_model)
    if not ok and not request.form.get("save_anyway"):
        error = f"Not saved. This provider is live for customers and the test failed: {message}"
        if api_key.strip():
            error += " Re-enter the API key to try again."
        return _render(400, form_state={provider: {"model": model, "error": error, "offer_save_anyway": True}})

    ai_provider_service.save_credentials(
        provider,
        api_key=api_key,
        model=model,
        updated_by=_admin_id(),
        test_result=ai_provider_service.TEST_OK if ok else ai_provider_service.TEST_FAILED,
    )
    if ok:
        flash(f"Saved and tested. {message}", "success")
    else:
        flash(f"Saved anyway, but the test failed: {message}", "error")
    return _back_to(provider)


def _save_inactive(provider, api_key, model):
    view = ai_provider_service.save_credentials(provider, api_key=api_key, model=model, updated_by=_admin_id())
    if view["key_status"] != "saved":
        flash("Saved. Add an API key to test the connection.", "success")
        return _back_to(provider)

    key, test_model = ai_provider_service.credentials_for_test(provider, api_key=None, model=None)
    ok, message = registry.check_connection(provider, api_key=key, model=test_model)
    ai_provider_service.record_test_result(provider, ok)
    flash(f"Saved. {message}" if ok else f"Saved, but the connection test failed: {message}", "success" if ok else "error")
    return _back_to(provider)


@bp.post("/settings/ai/<provider>/remove-key")
def ai_settings_remove_key(provider):
    try:
        ai_provider_service.remove_key(provider, updated_by=_admin_id())
    except AppError as err:
        return _render(err.status_code, form_state={provider: {"error": err.message}})
    flash("API key removed.", "success")
    return _back_to(provider)


@bp.post("/settings/ai/active")
def ai_settings_set_active():
    provider = request.form.get("provider", "")
    try:
        view = ai_provider_service.get_provider(provider)
        if view["active"]:
            flash(f"{view['display_name']} is already the active provider.", "success")
            return _back_to()
        if (
            view["configured"]
            and view["last_test_result"] != ai_provider_service.TEST_OK
            and not request.form.get("confirm_untested")
        ):
            return _render(
                400,
                activate_state={
                    "provider": provider,
                    "warning": f"{view['display_name']} hasn't passed a connection test. "
                    "Customers will start using it immediately. Tick “Activate anyway” to continue, "
                    "or run Test connection first.",
                },
            )
        ai_provider_service.set_active(provider, updated_by=_admin_id())
    except AppError as err:
        return _render(err.status_code, activate_state={"provider": provider, "error": err.message})
    flash(f"{view['display_name']} is now the active AI provider.", "success")
    return _back_to()
