"""Чтение и запись простых настроек из app_settings.

Интервал опроса можно поменять без правки .env и без перезапуска
кода: планировщик при каждом цикле спрашивает get_poll_interval_seconds().
APScheduler в scheduler_worker пересоздаёт интервал, прочитав это значение.
"""

from flask import current_app

from app.extensions import db
from app.models import POLL_INTERVAL_KEY, AppSetting

MIN_POLL_SECONDS = 30
MAX_POLL_SECONDS = 24 * 60 * 60


def get_poll_interval_seconds() -> int:
    row = db.session.get(AppSetting, POLL_INTERVAL_KEY)
    if row is not None:
        try:
            value = int(row.value)
        except (TypeError, ValueError):
            value = int(current_app.config.get("POLL_INTERVAL_SECONDS", 300))
    else:
        value = int(current_app.config.get("POLL_INTERVAL_SECONDS", 300))
    return max(MIN_POLL_SECONDS, min(MAX_POLL_SECONDS, value))


def set_poll_interval_seconds(value: int) -> int:
    value = int(value)
    if value < MIN_POLL_SECONDS or value > MAX_POLL_SECONDS:
        raise ValueError(
            f"Интервал должен быть от {MIN_POLL_SECONDS} до {MAX_POLL_SECONDS} секунд."
        )
    row = db.session.get(AppSetting, POLL_INTERVAL_KEY)
    if row is None:
        row = AppSetting(key=POLL_INTERVAL_KEY, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)
    db.session.commit()
    return value
