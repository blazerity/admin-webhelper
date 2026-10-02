"""Библиотека скриптов и просмотр лога запуска.

Эндпоинты:
- list_scripts    GET  /scripts/
- new_script      GET  /scripts/new
- create_script   POST /scripts/
- edit_script     GET  /scripts/<script_id>/edit
- update_script   POST /scripts/<script_id>
- delete_script   POST /scripts/<script_id>/delete
- run_script      POST /scripts/<script_id>/run
- run_detail      GET  /scripts/runs/<run_id>
- run_status      GET  /scripts/runs/<run_id>/status
- cancel_run      POST /scripts/runs/<run_id>/cancel
- close_session   POST /scripts/runs/<run_id>/close-session

Библиотека и удалённый запуск скрипта — только для администратора.
Страницу лога открывает и тот, кто нажал Ping: он может не быть
администратором. Поэтому run_detail и run_status не используют
admin_required.

static/js/run_log.js опрашивает run_status, пока запуск не закончился.
Позже этот опрос можно заменить на SSE или WebSocket, не трогая журнал.
"""

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.authz import (
    admin_required,
    get_visible_device_or_404,
    get_visible_sector_or_404,
    user_can_access_device,
)
from app.extensions import db
from app.models import Device, RunStatus, RunType, Script, ScriptRun, Sector
from app.services import script_service
from app.services.credential_service import get_stored_credential

bp = Blueprint("scripts", __name__, url_prefix="/scripts")

_STATUS_LABELS = {
    RunStatus.PENDING: "ожидание",
    RunStatus.RUNNING: "выполняется",
    RunStatus.SUCCESS: "успешно",
    RunStatus.FAILED: "ошибка",
    RunStatus.CANCELLED: "остановлен",
}

_RUN_TYPE_LABELS = {
    RunType.PING: "Ping",
    RunType.TRACERT: "Трассировка",
    RunType.COMMAND: "Команда",
    RunType.SCRIPT: "Скрипт",
}

_SESSION_HINTS = {
    "busy": "Кнопка «Завершить сессию» станет активной, когда команда закончится.",
    "open": "Сессия открыта. Её можно завершить. Без кнопки она закроется сама после простоя.",
    "closed": "Сессия закрыта.",
}


def _render_form(script: Script | None):
    devices = Device.query.order_by(Device.ip).all()
    sectors = Sector.query.order_by(Sector.name).all()
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
        user_id=current_user.id,
    )


def _optional_int(field: str) -> int | None:
    raw = (request.form.get(field) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _viewer_can_see(run: ScriptRun) -> bool:
    if getattr(current_user, "is_admin", False):
        return True
    if run.user_id is not None and run.user_id == current_user.id:
        return True
    return user_can_access_device(current_user, run.device)


def _format_dt(value) -> str:
    if not value:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


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


def _history_label(item: ScriptRun) -> str:
    if item.script is not None and item.script.name:
        return item.script.name
    text = " ".join((item.command_text or "").split())
    if len(text) > 72:
        text = text[:72] + "…"
    kind = _RUN_TYPE_LABELS.get(item.run_type, item.run_type)
    if text:
        return f"{kind}: {text}"
    return kind


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
            "label": _history_label(item),
            "status_label": _STATUS_LABELS.get(item.status, item.status),
            "when": _format_dt(item.started_at),
        }
        for item in rows
        if _viewer_can_see(item)
    ]


def _session_hint(run: ScriptRun, state: str) -> str:
    if state == "closed" and run.run_type not in (RunType.COMMAND, RunType.SCRIPT):
        return "Удалённой SMB-сессии нет: эта проверка идёт с сервера приложения."
    return _SESSION_HINTS.get(state, _SESSION_HINTS["closed"])


def _run_for_viewer(run_id: int) -> ScriptRun:
    run = db.session.get(ScriptRun, run_id)
    if run is None:
        abort(404)
    if not _viewer_can_see(run):
        abort(403)
    return run


@bp.get("/")
@admin_required
def list_scripts():
    scripts = Script.query.order_by(Script.name).all()
    return render_template("scripts/list.html", scripts=scripts)


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
    flash("Скрипт сохранён.", "success")
    return redirect(url_for("scripts.edit_script", script_id=script.id))


@bp.get("/<int:script_id>/edit")
@admin_required
def edit_script(script_id: int):
    script = db.session.get(Script, script_id)
    if script is None:
        abort(404)
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
    flash("Скрипт сохранён.", "success")
    return redirect(url_for("scripts.edit_script", script_id=script.id))


@bp.post("/<int:script_id>/delete")
@admin_required
def delete_script(script_id: int):
    if db.session.get(Script, script_id) is None:
        abort(404)
    script_service.delete_script(script_id)
    flash("Скрипт удалён.", "success")
    return redirect(url_for("scripts.list_scripts"))


@bp.post("/<int:script_id>/run")
@admin_required
def run_script(script_id: int):
    script = db.session.get(Script, script_id)
    if script is None:
        abort(404)
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


@bp.get("/runs/<int:run_id>")
@login_required
def run_detail(run_id: int):
    run = _run_for_viewer(run_id)
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
        batch_runs = [item for item in siblings if _viewer_can_see(item)]
    finished = run.status in RunStatus.FINISHED
    state = script_service.run_session_state(run.id, finished)
    return render_template(
        "scripts/run.html",
        run=run,
        batch_runs=batch_runs,
        status_label=_STATUS_LABELS.get(run.status, run.status),
        run_type_label=_RUN_TYPE_LABELS.get(run.run_type, run.run_type),
        session_open=state == "open",
        session_state=state,
        session_hint=_session_hint(run, state),
        session_hint_closed=_session_hint(run, "closed"),
        finished=finished,
        pc_name=_pc_name(run),
        started_label=_format_dt(run.started_at),
        finished_label=_format_dt(run.finished_at),
        account_label=_account_label(run),
        history=_device_history(run),
        launched_by=_launched_by(run),
    )


@bp.post("/runs/<int:run_id>/cancel")
@login_required
def cancel_run(run_id: int):
    run = _run_for_viewer(run_id)
    script_service.cancel_run(run.id)
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.post("/runs/<int:run_id>/close-session")
@login_required
def close_run_session(run_id: int):
    run = _run_for_viewer(run_id)
    if script_service.close_run_session(run.id):
        flash("Сессия закрыта.", "success")
    else:
        flash("Открытой сессии нет.", "info")
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.get("/runs/<int:run_id>/status")
@login_required
def run_status(run_id: int):
    # Опрос делает static/js/run_log.js.
    # Позже его можно заменить на SSE или WebSocket.
    run = _run_for_viewer(run_id)
    finished = run.status in RunStatus.FINISHED
    state = script_service.run_session_state(run.id, finished)
    return jsonify(
        {
            "status": run.status,
            "status_label": _STATUS_LABELS.get(run.status, run.status),
            "log_text": run.log_text or "",
            "exit_code": run.exit_code,
            "finished": finished,
            "finished_at": _format_dt(run.finished_at) if run.finished_at else "",
            "session_open": state == "open",
            "session_state": state,
            "session_hint": _session_hint(run, state),
        }
    )
