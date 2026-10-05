"""Ключи app_settings для ежедневного опроса железа Windows."""

from __future__ import annotations

from dataclasses import dataclass

from app.extensions import db
from app.utils import as_truthy

HARDWARE_POLL_SCHEDULE_ENABLED = "hardware_poll_schedule_enabled"
HARDWARE_POLL_SCHEDULE_CRON = "hardware_poll_schedule_cron"

# Полдень локального TZ сервера — как в задаче («условно раз в день в полдень»).
DEFAULT_SCHEDULE_CRON = "0 12 * * *"


@dataclass(frozen=True)
class HardwarePollSettings:
    schedule_enabled: bool
    schedule_cron: str


def _get_raw(key: str) -> str | None:
    from app.services.settings_service import get_app_setting

    return get_app_setting(key)


def _set_raw(key: str, value: str) -> None:
    from app.services.settings_service import set_app_setting

    set_app_setting(key, value)


def _validate_cron(expr: str) -> None:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron должен быть из 5 полей: минута час день месяц день_недели")


def get_hardware_poll_settings() -> HardwarePollSettings:
    cron = (_get_raw(HARDWARE_POLL_SCHEDULE_CRON) or "").strip() or DEFAULT_SCHEDULE_CRON
    raw = _get_raw(HARDWARE_POLL_SCHEDULE_ENABLED)
    if raw is None or not str(raw).strip():
        schedule_enabled = True
    else:
        schedule_enabled = as_truthy(str(raw).strip(), default=True)
    return HardwarePollSettings(
        schedule_enabled=schedule_enabled,
        schedule_cron=cron,
    )


def set_hardware_poll_settings(
    *,
    schedule_enabled: bool | None = None,
    schedule_cron: str | None = None,
) -> HardwarePollSettings:
    if schedule_enabled is not None:
        _set_raw(HARDWARE_POLL_SCHEDULE_ENABLED, "1" if schedule_enabled else "0")

    if schedule_cron is not None:
        cron = schedule_cron.strip() or DEFAULT_SCHEDULE_CRON
        _validate_cron(cron)
        _set_raw(HARDWARE_POLL_SCHEDULE_CRON, cron)

    db.session.commit()
    return get_hardware_poll_settings()
