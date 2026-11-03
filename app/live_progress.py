"""Общий поток шагов для долгих кнопок на карточке.

Форма без скрипта отвечает редиректом. Если клиент просит
application/x-ndjson, та же работа отдаёт короткие строки по мере выполнения.
Итог кладётся в подписанную cookie и показывается как обычное сообщение
после перехода на карточку.
"""

from __future__ import annotations

import json
import queue
import threading

from flask import Response, current_app, request, stream_with_context
from itsdangerous import BadData, URLSafeTimedSerializer

from app.extensions import db

LIVE_NOTICE_COOKIE = "bawh_check_notice"
_NOTICE_MAX_AGE = 120
_JOIN_TIMEOUT_S = 180


def wants_live_progress() -> bool:
    return "application/x-ndjson" in (request.headers.get("Accept") or "")


def pack_live_notice(
    device_id: int | None,
    category: str,
    message: str,
    *,
    scope: str | None = None,
) -> str:
    text = (message or "").strip()
    if len(text) > 800:
        text = text[:799].rstrip() + "…"
    payload: dict = {"c": category, "m": text}
    if scope:
        payload["s"] = scope
    else:
        payload["d"] = int(device_id or 0)
    return _serializer().dumps(payload)


def consume_live_notice(
    *,
    device_id: int | None = None,
    scope: str | None = None,
) -> tuple[tuple[str, str] | None, bool]:
    """Сообщение и флаг «cookie можно стереть».

    Чужой scope не стираем: страница карточки не должна съесть итог опроса сети.
    """
    raw = request.cookies.get(LIVE_NOTICE_COOKIE)
    if not raw:
        return None, False
    try:
        data = _serializer().loads(raw, max_age=_NOTICE_MAX_AGE)
    except BadData:
        return None, True
    if not isinstance(data, dict):
        return None, True
    notice = _notice_from(data)
    if scope:
        if data.get("s") != scope or notice is None:
            return None, data.get("s") == scope
        return notice, True
    if data.get("s"):
        return None, False
    if data.get("d") != device_id or notice is None:
        return None, True
    return notice, True


def pop_live_notice(device_id: int) -> tuple[str, str] | None:
    """Сообщение для этой карточки. Подпись не даёт подменить текст."""
    notice, _clear = consume_live_notice(device_id=device_id)
    return notice


def stream_live_progress(
    device_id: int,
    redirect_url: str,
    work,
    *,
    first_step: str,
    scope: str | None = None,
    timeout_s: int = _JOIN_TIMEOUT_S,
):
    """Выполнить work(on_progress) в потоке и отдать NDJSON.

    work возвращает пару (категория, текст). Исключение становится
    сообщением «не удалось».
    """
    app = current_app._get_current_object()
    steps: queue.Queue = queue.Queue()

    def runner() -> None:
        category = "danger"
        message = "Не удалось выполнить операцию."
        with app.app_context():
            try:
                category, message = work(lambda text: steps.put(("step", text)))
            except Exception:
                app.logger.exception("Сбой операции на устройстве %s", device_id)
            finally:
                db.session.remove()
        steps.put(("done", category, message))

    threading.Thread(
        target=runner,
        name=f"live-progress-{device_id}",
        daemon=True,
    ).start()

    def generate():
        yield _line({"type": "step", "text": first_step})
        while True:
            try:
                item = steps.get(timeout=timeout_s)
            except queue.Empty:
                category, message = "warning", "Операция заняла слишком много времени."
                break
            if item[0] == "step":
                yield _line({"type": "step", "text": item[1]})
                continue
            category, message = item[1], item[2]
            break
        notice = pack_live_notice(device_id, category, message, scope=scope)
        yield _line({"type": "done", "notice": notice, "redirect": redirect_url})

    response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _notice_from(data: dict) -> tuple[str, str] | None:
    category = data.get("c") or "info"
    if category not in {"success", "danger", "warning", "info"}:
        category = "info"
    message = (data.get("m") or "").strip()
    if not message:
        return None
    return category, message


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="bawh-check-notice")


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
