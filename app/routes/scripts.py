"""Библиотека скриптов и просмотр лога запуска.

CRUD — admin_required; список и запуск — user_can_run_scripts /
user_can_run_script (operator: только опубликованные).
run_detail/status доступны автору Ping и тем, кто видит устройство.
"""

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.authz import (
    accessible_devices,
    accessible_sectors,
    admin_required,
    get_visible_device_or_404,
    get_visible_script_run_or_404,
    get_visible_sector_or_404,
    user_can_run_script,
    user_can_run_scripts,
    user_can_see_script_run,
)
from app.extensions import db
from app.models import Device, RunStatus, RunType, Script, ScriptRun
from app.run_display import (
    run_launch_label,
    run_status_label,
    run_type_label,
    run_when_label,
)
from app.services import audit_service, batch_service, script_service
from app.services.credential_service import get_stored_credential
from app.utils import parse_optional_int

bp = Blueprint("scripts", __name__, url_prefix="/scripts")

_SESSION_HINTS = {
    "busy": "Кнопка «Завершить сессию» станет активной, когда команда закончится.",
    "open": "Сессия открыта. Её можно завершить. Без кнопки она закроется сама после простоя.",
    "closed": "Сессия закрыта.",
}


def _render_form(script: Script | None):
    # Селекты только по доступным устройствам/секторам (W3 harden).
    devices = accessible_devices(current_user)
    sectors = accessible_sectors(current_user)
    script_body = ""
    if script is not None:
        try:
            script_body = script_service.get_script_body(script)
        except script_service.ScriptError as exc:
            flash(str(exc), "warning")
            script_body = script.content or ""
    return render_template(
        "scripts/form.html",
        script=script,
        script_body=script_body,
        devices=devices,
        sectors=sectors,
        can_manage_scripts=bool(getattr(current_user, "is_admin", False)),
    )


def _form_fields():
    return dict(
        name=request.form.get("name", ""),
        description=request.form.get("description", ""),
        target_os=request.form.get("target_os", "windows"),
        interpreter=request.form.get("interpreter", "powershell"),
        storage=request.form.get("storage", "db"),
        run_as=request.form.get("run_as", "psexec"),
        content=request.form.get("content", ""),
        is_published=request.form.get("is_published") == "1",
        user_id=current_user.id,
    )


def _optional_int(field: str) -> int | None:
    return parse_optional_int(request.form.get(field))


def _launched_by(run: ScriptRun) -> str:
    user = run.user
    if user is None:
        return "—"
    return user.display_name or user.username or "—"


def _pc_name(run: ScriptRun) -> str:
    device = run.device
    if device is None:
        return "Компьютер"
    return device.hostname or device.ip


def _account_label(run: ScriptRun) -> str | None:
    """Имя учётки SMB без пароля. Пароль для подписи страницы не расшифровывается."""
    if not getattr(current_user, "is_admin", False):
        return None
    if run.run_type not in (RunType.COMMAND, RunType.SCRIPT) or not run.user_id:
        return None
    row = get_stored_credential(run.user_id)
    if row is not None and row.username and row.password_encrypted:
        if row.domain:
            return f"{row.domain}\\{row.username}"
        return row.username
    if row is not None and row.login_password_encrypted and run.user is not None:
        domain = (current_app.config.get("LDAP_DOMAIN") or "").strip()
        name = (run.user.username or "").strip()
        if not name:
            return None
        return f"{domain}\\{name}" if domain else name
    return None


def _device_history(run: ScriptRun) -> list[dict]:
    if not run.device_id:
        return []
    rows = (
        ScriptRun.query.options(selectinload(ScriptRun.script))
        .filter(ScriptRun.device_id == run.device_id)
        .order_by(ScriptRun.started_at.desc(), ScriptRun.id.desc())
        .limit(30)
        .all()
    )
    return [
        {
            "run": item,
            "label": run_launch_label(item),
            "status_label": run_status_label(item.status),
            "when": run_when_label(item.started_at),
        }
        for item in rows
        if user_can_see_script_run(current_user, item)
    ]


def _session_hint(run: ScriptRun, state: str) -> str:
    if state == "closed" and run.run_type not in (RunType.COMMAND, RunType.SCRIPT):
        return "Удалённой SMB-сессии нет: эта проверка идёт с сервера приложения."
    return _SESSION_HINTS.get(state, _SESSION_HINTS["closed"])


def _scripts_for_user():
    query = Script.query.order_by(Script.name)
    if not getattr(current_user, "is_admin", False):
        query = query.filter(Script.is_published.is_(True))
    return query.all()


@bp.get("/")
@login_required
def list_scripts():
    if not user_can_run_scripts(current_user):
        abort(403)
    scripts = _scripts_for_user()
    return render_template(
        "scripts/list.html",
        scripts=scripts,
        can_manage_scripts=bool(getattr(current_user, "is_admin", False)),
    )


@bp.get("/new")
@admin_required
def new_script():
    return _render_form(None)


@bp.post("/")
@admin_required
def create_script():
    try:
        script = script_service.save_script(**_form_fields())
    except script_service.ScriptError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("scripts.new_script"))
    audit_service.log(
        current_user,
        "create",
        "script",
        script.id,
        detail=script.name,
    )
    flash("Скрипт сохранён.", "success")
    return redirect(url_for("scripts.edit_script", script_id=script.id))


@bp.get("/<int:script_id>/edit")
@login_required
def edit_script(script_id: int):
    """Admin — правка; operator — просмотр/запуск опубликованного."""
    script = db.session.get(Script, script_id)
    if script is None:
        abort(404)
    if getattr(current_user, "is_admin", False):
        return _render_form(script)
    if not user_can_run_script(current_user, script):
        abort(403)
    return _render_form(script)


@bp.post("/<int:script_id>")
@admin_required
def update_script(script_id: int):
    script = db.session.get(Script, script_id)
    if script is None:
        abort(404)
    try:
        script_service.save_script(**_form_fields(), script=script)
    except script_service.ScriptError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("scripts.edit_script", script_id=script.id))
    audit_service.log(
        current_user,
        "update",
        "script",
        script.id,
        detail=script.name,
    )
    flash("Скрипт сохранён.", "success")
    return redirect(url_for("scripts.edit_script", script_id=script.id))


@bp.post("/<int:script_id>/delete")
@admin_required
def delete_script(script_id: int):
    script = db.session.get(Script, script_id)
    if script is None:
        abort(404)
    name = script.name
    script_service.delete_script(script_id)
    audit_service.log(
        current_user,
        "delete",
        "script",
        script_id,
        detail=name,
    )
    flash("Скрипт удалён.", "success")
    return redirect(url_for("scripts.list_scripts"))


@bp.post("/<int:script_id>/run")
@login_required
def run_script(script_id: int):
    script = db.session.get(Script, script_id)
    if script is None:
        abort(404)
    if not user_can_run_script(current_user, script):
        abort(403)
    if script.target_os == "linux" or script.interpreter == "bash":
        flash(
            "Удалённый Linux в v1 не реализован: PsExec работает только с Windows.",
            "danger",
        )
        return redirect(url_for("scripts.edit_script", script_id=script.id))

    device_id = _optional_int("device_id")
    sector_id = _optional_int("sector_id")
    if device_id is None and sector_id is None:
        flash("Выберите устройство или сектор.", "warning")
        return redirect(url_for("scripts.edit_script", script_id=script.id))

    chosen: dict[int, Device] = {}
    if sector_id is not None:
        sector = get_visible_sector_or_404(sector_id)
        for device in sector.devices:
            chosen[device.id] = device
    if device_id is not None:
        device = get_visible_device_or_404(device_id)
        chosen[device.id] = device
    devices = sorted(chosen.values(), key=lambda item: (item.ip, item.id))
    if not devices:
        flash("В выбранном секторе нет устройств.", "warning")
        return redirect(url_for("scripts.edit_script", script_id=script.id))

    try:
        runs = script_service.start_script_on_devices(script, current_user, devices)
    except script_service.ScriptError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("scripts.edit_script", script_id=script.id))
    return redirect(url_for("scripts.run_detail", run_id=runs[0].id))


@bp.get("/batches/<batch_id>")
@login_required
def batch_detail(batch_id: str):
    """SSR прогресс пачки. Разметку дополняет A2; минимум — context + polling URL."""
    payload = batch_service.batch_status_payload(current_user, batch_id)
    if payload is None:
        abort(404)
    return render_template(
        "scripts/batch.html",
        batch_id=payload["batch_id"],
        total=payload["total"],
        pending=payload["pending"],
        running=payload["running"],
        success=payload["success"],
        failed=payload["failed"],
        cancelled=payload["cancelled"],
        finished=payload["finished"],
        runs=payload["runs"],
        status_url=url_for("devices.batch_status", batch_id=batch_id),
    )


@bp.get("/runs/<int:run_id>")
@login_required
def run_detail(run_id: int):
    run = get_visible_script_run_or_404(run_id)
    batch_runs = []
    if run.batch_id:
        siblings = (
            ScriptRun.query.filter(
                ScriptRun.batch_id == run.batch_id,
                ScriptRun.id != run.id,
            )
            .order_by(ScriptRun.id)
            .all()
        )
        batch_runs = [item for item in siblings if user_can_see_script_run(current_user, item)]
    finished = run.status in RunStatus.FINISHED
    state = script_service.run_session_state(run.id, finished)
    return render_template(
        "scripts/run.html",
        run=run,
        batch_runs=batch_runs,
        status_label=run_status_label(run.status),
        run_type_label=run_type_label(run.run_type),
        session_open=state == "open",
        session_state=state,
        session_hint=_session_hint(run, state),
        session_hint_closed=_session_hint(run, "closed"),
        finished=finished,
        pc_name=_pc_name(run),
        started_label=run_when_label(run.started_at),
        finished_label=run_when_label(run.finished_at),
        account_label=_account_label(run),
        history=_device_history(run),
        launched_by=_launched_by(run),
    )


@bp.post("/runs/<int:run_id>/cancel")
@login_required
def cancel_run(run_id: int):
    run = get_visible_script_run_or_404(run_id)
    script_service.cancel_run(run.id)
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.post("/runs/<int:run_id>/close-session")
@login_required
def close_run_session(run_id: int):
    run = get_visible_script_run_or_404(run_id)
    if script_service.close_run_session(run.id):
        flash("Сессия закрыта.", "success")
    else:
        flash("Открытой сессии нет.", "info")
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.get("/runs/<int:run_id>/status")
@login_required
def run_status(run_id: int):
    run = get_visible_script_run_or_404(run_id)
    finished = run.status in RunStatus.FINISHED
    state = script_service.run_session_state(run.id, finished)
    return jsonify(
        {
            "status": run.status,
            "status_label": run_status_label(run.status),
            "log_text": run.log_text or "",
            "exit_code": run.exit_code,
            "finished": finished,
            "finished_at": run_when_label(run.finished_at) if run.finished_at else "",
            "session_open": state == "open",
            "session_state": state,
            "session_hint": _session_hint(run, state),
        }
    )
