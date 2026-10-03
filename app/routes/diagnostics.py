"""Кнопки на карточке устройства: ping, трассировка, удалённая команда.

Эндпоинты:
- ping_device     POST /devices/<device_id>/ping
- tracert_device  POST /devices/<device_id>/tracert
- command_device  POST /devices/<device_id>/command

Ping и трассировка — кому видно устройство и не чистый viewer.
Команда — только администратору: она выполняется на машине из инвентаря.
В script_runs.command_text пишется сама команда, без пароля PsExec.
"""

from flask import Blueprint, abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.authz import admin_required, get_visible_device_or_404, user_can_run_diagnostics
from app.models import RunType
from app.services.script_service import start_run

bp = Blueprint("diagnostics", __name__)


@bp.post("/devices/<int:device_id>/ping")
@login_required
def ping_device(device_id: int):
    if not user_can_run_diagnostics(current_user):
        abort(403)
    device = get_visible_device_or_404(device_id)
    run = start_run(RunType.PING, current_user, device, f"ping {device.ip}")
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.post("/devices/<int:device_id>/tracert")
@login_required
def tracert_device(device_id: int):
    if not user_can_run_diagnostics(current_user):
        abort(403)
    device = get_visible_device_or_404(device_id)
    run = start_run(RunType.TRACERT, current_user, device, f"tracert {device.ip}")
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.post("/devices/<int:device_id>/command")
@admin_required
def command_device(device_id: int):
    device = get_visible_device_or_404(device_id)
    command = (request.form.get("command") or "").strip()
    limit = int(current_app.config.get("MAX_REMOTE_COMMAND_CHARS", 4_000))
    if not command:
        flash("Введите команду.", "warning")
        return redirect(url_for("devices.detail", device_id=device.id))
    if len(command) > limit:
        flash(f"Команда длиннее {limit} символов.", "warning")
        return redirect(url_for("devices.detail", device_id=device.id))
    run = start_run(RunType.COMMAND, current_user, device, command)
    return redirect(url_for("scripts.run_detail", run_id=run.id))
