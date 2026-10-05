"""Ключи app_settings и чтение/запись настроек «Отчёты о ПК».

SMTP переиспользуется из модуля паролей AD (`get_smtp_settings`).
Ключи sector_daily_* сохранены для совместимости с уже развёрнутыми БД.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.extensions import db
from app.utils import as_truthy

SECTOR_DAILY_SCHEDULE_ENABLED = "sector_daily_schedule_enabled"
SECTOR_DAILY_SCHEDULE_CRON = "sector_daily_schedule_cron"
SECTOR_DAILY_RECIPIENTS = "sector_daily_recipients"

DEFAULT_SCHEDULE_CRON = "0 7 * * *"


@dataclass(frozen=True)
class SectorDailyReportSettings:
    """Параметры ежедневной рассылки отчётов о ПК."""

    schedule_enabled: bool
    schedule_cron: str
    recipients: list[str]


def _get_raw(key: str) -> str | None:
    from app.services.settings_service import get_app_setting

    return get_app_setting(key)


def _set_raw(key: str, value: str) -> None:
    from app.services.settings_service import set_app_setting

    set_app_setting(key, value)


def _split_list(raw: str, sep: str = ",") -> list[str]:
    return [item.strip() for item in (raw or "").split(sep) if item.strip()]


def _validate_cron(expr: str) -> None:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron должен быть из 5 полей: минута час день месяц день_недели")


def get_sector_daily_report_settings() -> SectorDailyReportSettings:
    cron = (_get_raw(SECTOR_DAILY_SCHEDULE_CRON) or "").strip() or DEFAULT_SCHEDULE_CRON
    enabled_raw = (_get_raw(SECTOR_DAILY_SCHEDULE_ENABLED) or "0").strip().lower()
    schedule_enabled = as_truthy(enabled_raw, default=False)
    return SectorDailyReportSettings(
        schedule_enabled=schedule_enabled,
        schedule_cron=cron,
        recipients=_split_list(_get_raw(SECTOR_DAILY_RECIPIENTS) or ""),
    )


def set_sector_daily_report_settings(
    *,
    schedule_enabled: bool | None = None,
    schedule_cron: str | None = None,
    recipients: str | None = None,
) -> SectorDailyReportSettings:
    """Обновить переданные поля настроек отчёта."""
    if schedule_enabled is not None:
        _set_raw(SECTOR_DAILY_SCHEDULE_ENABLED, "1" if schedule_enabled else "0")

    if schedule_cron is not None:
        cron = schedule_cron.strip() or DEFAULT_SCHEDULE_CRON
        _validate_cron(cron)
        _set_raw(SECTOR_DAILY_SCHEDULE_CRON, cron)

    if recipients is not None:
        _set_raw(SECTOR_DAILY_RECIPIENTS, recipients.strip())

    db.session.commit()
    return get_sector_daily_report_settings()
