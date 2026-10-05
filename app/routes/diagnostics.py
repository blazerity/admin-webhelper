"""Кнопки на карточке устройства: ping, трассировка, удалённая команда.

Эндпоинты:
- check_device    POST /devices/<device_id>/check — быстрый ICMP → device_history
- ping_device     POST /devices/<device_id>/ping
- tracert_device  POST /devices/<device_id>/tracert
- command_device  POST /devices/<device_id>/command

Ping и трассировка — кому видно устройство и не чистый viewer.
Команда — только администратору: она выполняется на машине из инвентаря.
В script_runs.command_text пишется сама команда, без пароля PsExec.
"""

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.authz import admin_required, get_visible_device_or_404, user_can_run_diagnostics
from app.models import RunType
from app.services.net_utils import NetworkInputError
from app.services.ping_service import check_device
from app.services.script_service import start_run

bp = Blueprint("diagnostics", __name__)


def _wants_json() -> bool:
    if request.is_json:
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and (
        request.accept_mimetypes[best]
        > request.accept_mimetypes["text/html"]
    )


@bp.post("/devices/<int:device_id>/check")
@login_required
def check_device_status(device_id: int):
    """Быстрая проверка доступности: ICMP + запись в историю опросов."""
    if not user_can_run_diagnostics(current_user):
        abort(403)
    device = get_visible_device_or_404(device_id)
    tab = (request.form.get("tab") or "overview").strip() or "overview"
    try:
        payload = check_device(device)
    except NetworkInputError as exc:
        if _wants_json():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "warning")
        return redirect(url_for("devices.detail", device_id=device.id, tab=tab))
    except Exception:
        current_app.logger.exception("Не удалось проверить устройство %s", device_id)
        if _wants_json():
            return jsonify({"error": "Не удалось выполнить проверку."}), 500
        flash("Не удалось выполнить проверку.", "danger")
        return redirect(url_for("devices.detail", device_id=device.id, tab=tab))

    if _wants_json():
        return jsonify(payload)

    label = {"online": "доступен", "offline": "недоступен"}.get(
        payload["status"], "неизвестно"
    )
    if payload["changed"]:
        flash(f"Статус изменился: устройство {label}.", "success")
    else:
        flash(f"Устройство {label}.", "info")
    return redirect(url_for("devices.detail", device_id=device.id, tab=tab))


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
