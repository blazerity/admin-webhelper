"""CRUD сервисов для блока доступности на экране входа.

Эндпоинты (админ):
- list_services   GET  /login-services/
- new_service     GET  /login-services/new
- create_service  POST /login-services/
- edit_service    GET  /login-services/<id>/edit
- update_service  POST /login-services/<id>
- delete_service  POST /login-services/<id>/delete

Публичный JSON (без авторизации):
- status          GET  /api/login-services/status
"""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from app.authz import admin_required
from app.extensions import db
from app.models import LoginService
from app.services.login_service_status import LoginServiceError
from app.services.login_service_status import check_enabled_services
from app.services.login_service_status import delete_service as remove_service
from app.services.login_service_status import save_service
from app.services.net_utils import NetworkInputError

bp = Blueprint("login_services", __name__)


@bp.get("/login-services/")
@admin_required
def list_services():
    """Список переехал в Настройки → Общие."""
    return redirect(url_for("admin.settings", section="general") + "#login-services")


@bp.get("/login-services/new")
@admin_required
def new_service():
    return render_template("login_services/form.html", service=None)


@bp.post("/login-services/")
@admin_required
def create_service():
    return _save_from_form(None)


@bp.get("/login-services/<int:service_id>/edit")
@admin_required
def edit_service(service_id: int):
    return render_template(
        "login_services/form.html",
        service=_existing(service_id),
    )


@bp.post("/login-services/<int:service_id>")
@admin_required
def update_service(service_id: int):
    _existing(service_id)
    return _save_from_form(service_id)


@bp.post("/login-services/<int:service_id>/delete")
@admin_required
def delete_service(service_id: int):
    service = _existing(service_id)
    name = service.name
    try:
        remove_service(service_id)
    except LoginServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("login_services.list_services"))
    flash(f"Сервис «{name}» удалён.", "success")
    return redirect(url_for("login_services.list_services"))


@bp.get("/api/login-services/status")
def status():
    """Доступность включённых сервисов — для панели на /login."""
    items = check_enabled_services()
    return jsonify(
        {
            "services": [
                {
                    "id": item.id,
                    "name": item.name,
                    "address": item.address,
                    "online": item.online,
                    "latency_ms": item.latency_ms,
                }
                for item in items
            ]
        }
    )


def _existing(service_id: int) -> LoginService:
    service = db.session.get(LoginService, service_id)
    if service is None:
        abort(404)
    return service


def _save_from_form(service_id: int | None):
    name, address, sort_order, is_enabled = _form_values()
    try:
        service = save_service(service_id, name, address, sort_order, is_enabled)
    except (LoginServiceError, NetworkInputError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        draft = LoginService(
            name=(name or "").strip(),
            address=(address or "").strip(),
            sort_order=_draft_order(sort_order),
            is_enabled=is_enabled,
        )
        if service_id is not None:
            draft.id = service_id
        return render_template("login_services/form.html", service=draft)
    if service_id is None:
        flash(f"Сервис «{service.name}» добавлен.", "success")
    else:
        flash(f"Сервис «{service.name}» сохранён.", "success")
    return redirect(url_for("login_services.list_services"))


def _form_values() -> tuple[str, str, str, bool]:
    return (
        request.form.get("name", ""),
        request.form.get("address", ""),
        request.form.get("sort_order", "0"),
        request.form.get("is_enabled") == "on",
    )


def _draft_order(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
