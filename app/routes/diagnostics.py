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

import json
import queue
import threading

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    request,
    stream_with_context,
    url_for,
)
from flask_login import current_user, login_required
from itsdangerous import BadData, URLSafeTimedSerializer

from app.authz import admin_required, get_visible_device_or_404, user_can_run_diagnostics
from app.extensions import db
from app.models import Device, RunType
from app.services.net_utils import NetworkInputError
from app.services.ping_service import check_device
from app.services.script_service import start_run

bp = Blueprint("diagnostics", __name__)

CHECK_NOTICE_COOKIE = "bawh_check_notice"
_CHECK_NOTICE_MAX_AGE = 120
_CHECK_JOIN_TIMEOUT_S = 180


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


def _notice_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="bawh-check-notice")


def pack_check_notice(device_id: int, category: str, message: str) -> str:
    text = (message or "").strip()
    if len(text) > 800:
        text = text[:799].rstrip() + "…"
    return _notice_serializer().dumps({"d": int(device_id), "c": category, "m": text})


def pop_check_notice(device_id: int) -> tuple[str, str] | None:
    """Сообщение проверки для этой карточки. Подпись не даёт подменить текст."""
    raw = request.cookies.get(CHECK_NOTICE_COOKIE)
    if not raw:
        return None
    try:
        data = _notice_serializer().loads(raw, max_age=_CHECK_NOTICE_MAX_AGE)
    except BadData:
        return None
    if not isinstance(data, dict) or data.get("d") != device_id:
        return None
    category = data.get("c") or "info"
    if category not in {"success", "danger", "warning", "info"}:
        category = "info"
    message = (data.get("m") or "").strip()
    if not message:
        return None
    return category, message


def _wants_progress() -> bool:
    return "application/x-ndjson" in (request.headers.get("Accept") or "")


def _ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _run_check(app, device_id: int, steps: queue.Queue) -> None:
    """Поиск в отдельном потоке: генератор ответа должен отдавать шаги сразу."""
    payload = None
    error: tuple[str, str] | None = None
    with app.app_context():
        try:
            fresh = db.session.get(Device, device_id)
            if fresh is None:
                error = ("Устройство не найдено.", "warning")
            else:
                payload = check_device(
                    fresh,
                    on_progress=lambda text: steps.put(("step", text)),
                )
        except NetworkInputError as exc:
            error = (str(exc), "warning")
        except Exception:
            app.logger.exception("Не удалось проверить устройство %s", device_id)
            error = ("Не удалось выполнить проверку.", "danger")
        finally:
            db.session.remove()
    if error is not None:
        steps.put(("error", error[0], error[1]))
    else:
        steps.put(("result", payload))


def _stream_check(device_id: int, tab: str):
    app = current_app._get_current_object()
    steps: queue.Queue = queue.Queue()
    worker = threading.Thread(
        target=_run_check,
        args=(app, device_id, steps),
        name=f"device-check-{device_id}",
        daemon=True,
    )
    worker.start()
    redirect_url = url_for("devices.detail", device_id=device_id, tab=tab)

    def generate():
        yield _ndjson({"type": "step", "text": "Ищу компьютер…"})
        while True:
            try:
                item = steps.get(timeout=_CHECK_JOIN_TIMEOUT_S)
            except queue.Empty:
                category, message = "warning", "Проверка заняла слишком много времени."
                break
            kind = item[0]
            if kind == "step":
                yield _ndjson({"type": "step", "text": item[1]})
                continue
            if kind == "error":
                category, message = item[2], item[1]
            else:
                category, message = _check_message(item[1])
            break
        notice = pack_check_notice(device_id, category, message)
        yield _ndjson({"type": "done", "notice": notice, "redirect": redirect_url})

    response = Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.post("/devices/<int:device_id>/check")
@login_required
def check_device_status(device_id: int):
    """Проверка, что карточка — это та машина. История пишется в опросах."""
    if not user_can_run_diagnostics(current_user):
        abort(403)
    device = get_visible_device_or_404(device_id)
    tab = (request.form.get("tab") or "polls").strip() or "polls"
    # Сессия запроса не должна держать транзакцию, пока поток пишет статус.
    checked_id = device.id
    db.session.rollback()
    if _wants_progress():
        return _stream_check(checked_id, tab)
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
