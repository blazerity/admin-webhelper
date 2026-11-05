"""Кнопки на карточке устройства: ping, трассировка, удалённая команда.

Эндпоинты:
- check_device    POST /devices/<device_id>/check — поиск машины → device_history
- ping_device     POST /devices/<device_id>/ping
- tracert_device  POST /devices/<device_id>/tracert
- command_device  POST /devices/<device_id>/command

Обычная форма «Проверить сейчас» отвечает редиректом. Если клиент просит
application/x-ndjson, тот же поиск отдаёт шаги по мере работы.
В script_runs.command_text пишется сама команда, без пароля PsExec.
"""

from flask import Blueprint, abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.live_progress import stream_live_progress, wants_live_progress
from app.models import Device, RunType
from app.services.net_utils import NetworkInputError
from app.services.ping_service import check_device
from app.services.script_service import start_run

bp = Blueprint("diagnostics", __name__)


def _device_or_404(device_id: int) -> Device:
    device = db.session.get(Device, device_id)
    if device is None:
        abort(404)
    return device


def _check_message(payload: dict) -> tuple[str, str]:
    detail = (payload.get("detail") or "").strip()
    if detail and payload.get("status") != "online":
        return "warning", detail
    if detail:
        return "success", detail
    label = {"online": "доступен", "offline": "недоступен"}.get(
        payload.get("status"), "неизвестно"
    )
    if payload.get("changed"):
        return "success", f"Статус изменился: устройство {label}."
    return "info", f"Устройство {label}."


def _run_check(device_id: int, on_progress) -> tuple[str, str]:
    fresh = db.session.get(Device, device_id)
    if fresh is None:
        return "warning", "Устройство не найдено."
    try:
        payload = check_device(fresh, on_progress=on_progress)
    except NetworkInputError as exc:
        return "warning", str(exc)
    except Exception:
        current_app.logger.exception("Не удалось проверить устройство %s", device_id)
        return "danger", "Не удалось выполнить проверку."
    return _check_message(payload)


@bp.post("/devices/<int:device_id>/check")
@login_required
def check_device_status(device_id: int):
    """Проверка, что карточка — это та машина. История пишется в опросах."""
    device = _device_or_404(device_id)
    tab = (request.form.get("tab") or "polls").strip() or "polls"
    checked_id = device.id
    db.session.rollback()
    if wants_live_progress():
        return stream_live_progress(
            checked_id,
            url_for("devices.detail", device_id=checked_id, tab=tab),
            lambda on_progress: _run_check(checked_id, on_progress),
            first_step="Ищу компьютер…",
        )
    try:
        fresh = db.session.get(Device, checked_id)
        payload = check_device(fresh)
    except NetworkInputError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("devices.detail", device_id=checked_id, tab=tab))
    except Exception:
        current_app.logger.exception("Не удалось проверить устройство %s", device_id)
        flash("Не удалось выполнить проверку.", "danger")
        return redirect(url_for("devices.detail", device_id=checked_id, tab=tab))

    category, message = _check_message(payload)
    flash(message, category)
    return redirect(url_for("devices.detail", device_id=checked_id, tab=tab))


@bp.post("/devices/<int:device_id>/ping")
@login_required
def ping_device(device_id: int):
    device = _device_or_404(device_id)
    run = start_run(RunType.PING, current_user, device, f"ping {device.ip}")
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.post("/devices/<int:device_id>/tracert")
@login_required
def tracert_device(device_id: int):
    device = _device_or_404(device_id)
    run = start_run(RunType.TRACERT, current_user, device, f"tracert {device.ip}")
    return redirect(url_for("scripts.run_detail", run_id=run.id))


@bp.post("/devices/<int:device_id>/command")
@login_required
def command_device(device_id: int):
    device = _device_or_404(device_id)
    if not device.shows_commands:
        abort(404)
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
