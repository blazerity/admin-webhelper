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


def pack_live_notice(device_id: int, category: str, message: str) -> str:
    text = (message or "").strip()
    if len(text) > 800:
        text = text[:799].rstrip() + "…"
    return _serializer().dumps({"d": int(device_id), "c": category, "m": text})


def pop_live_notice(device_id: int) -> tuple[str, str] | None:
    """Сообщение для этой карточки. Подпись не даёт подменить текст."""
    raw = request.cookies.get(LIVE_NOTICE_COOKIE)
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=_NOTICE_MAX_AGE)
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


def stream_live_progress(device_id: int, redirect_url: str, work, *, first_step: str):
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
                item = steps.get(timeout=_JOIN_TIMEOUT_S)
            except queue.Empty:
                category, message = "warning", "Операция заняла слишком много времени."
                break
            if item[0] == "step":
                yield _line({"type": "step", "text": item[1]})
                continue
            category, message = item[1], item[2]
            break
        notice = pack_live_notice(device_id, category, message)
        yield _line({"type": "done", "notice": notice, "redirect": redirect_url})

    response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="bawh-check-notice")


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
