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

Библиотека и удалённый запуск скрипта — только для администратора.
Страницу лога открывает и тот, кто нажал Ping: он может не быть
администратором. Поэтому run_detail и run_status не используют
admin_required.

static/js/run_log.js опрашивает run_status каждые 1.5 секунды.
Позже этот опрос можно заменить на SSE или WebSocket, не трогая журнал.
"""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.authz import (
    admin_required,
    get_visible_device_or_404,
    get_visible_sector_or_404,
    user_can_access_device,
)
from app.extensions import db
from app.models import Device, RunStatus, Script, ScriptRun, Sector
from app.services import script_service

bp = Blueprint("scripts", __name__, url_prefix="/scripts")


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
    return render_template("scripts/run.html", run=run, batch_runs=batch_runs)


@bp.get("/runs/<int:run_id>/status")
@login_required
def run_status(run_id: int):
    # Опрос раз в 1.5 с делает static/js/run_log.js.
    # Позже его можно заменить на SSE или WebSocket.
    run = _run_for_viewer(run_id)
    return jsonify(
        {
            "status": run.status,
            "log_text": run.log_text or "",
            "exit_code": run.exit_code,
            "finished": run.status in (RunStatus.SUCCESS, RunStatus.FAILED),
        }
    )
