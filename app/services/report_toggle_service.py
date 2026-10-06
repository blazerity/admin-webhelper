"""Включение/выключение расписания отчётов + ensure отдельной службы.

Тумблер в UI включает job в APScheduler (флаг в app_settings) и при
включении поднимает свой systemd-unit:
  пароли AD → bawh-password-reports
  отчёты о ПК → bawh-pc-reports

Опрос сети живёт в bawh-scheduler и от тумблеров отчётов не зависит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.systemd_service import (
    PASSWORD_REPORTS_UNIT,
    PC_REPORTS_UNIT,
    SystemdError,
    UnitStatus,
    ensure_unit_running,
    get_unit_status,
    stop_unit,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportToggleResult:
    """Результат переключения тумблера рассылки."""

    enabled: bool
    message: str
    scheduler: UnitStatus
    scheduler_action: str = ""


def unit_status(unit: str) -> UnitStatus:
    return get_unit_status(unit)


def password_reports_status() -> UnitStatus:
    return get_unit_status(PASSWORD_REPORTS_UNIT)


def pc_reports_status() -> UnitStatus:
    return get_unit_status(PC_REPORTS_UNIT)


# Совместимость со старыми импортами в routes.
def scheduler_status() -> UnitStatus:
    return password_reports_status()


def apply_report_toggle(
    *,
    enabled: bool,
    set_enabled,
    label: str,
    unit: str,
) -> ReportToggleResult:
    """Сохранить флаг расписания и поднять/остановить службу модуля.

    set_enabled(bool) — колбэк модуля (password / sector report), пишет app_settings.
    Выключение тумблера останавливает только службу этого отчёта —
    опрос сети (bawh-scheduler) не трогаем.
    """
    set_enabled(bool(enabled))
    status = get_unit_status(unit)
    if not enabled:
        action = ""
        try:
            stopped = stop_unit(unit)
            status = stopped.status
            action = stopped.message
        except SystemdError as exc:
            logger.exception("%s: не удалось остановить %s", label, unit)
            return ReportToggleResult(
                enabled=False,
                message=(
                    f"{label}: рассылка выключена, но службу {unit} "
                    f"остановить не удалось: {exc}"
                ),
                scheduler=status,
                scheduler_action=str(exc),
            )
        return ReportToggleResult(
            enabled=False,
            message=(
                f"{label}: рассылка выключена. Отчёты по расписанию не уходят. "
                f"{action or f'Служба {unit}: {status.active_state}.'}"
            ),
            scheduler=status,
            scheduler_action=action,
        )

    action = ""
    try:
        ensure = ensure_unit_running(unit)
        status = ensure.status
        action = ensure.message
    except SystemdError as exc:
        logger.exception("%s: не удалось поднять %s", label, unit)
        return ReportToggleResult(
            enabled=True,
            message=(
                f"{label}: расписание включено, но службу {unit} "
                f"запустить не удалось: {exc}"
            ),
            scheduler=status,
            scheduler_action=str(exc),
        )

    parts = [
        f"{label}: сервис включён — отчёты будут приходить по расписанию.",
    ]
    if action:
        parts.append(action)
    elif status.available:
        parts.append(f"Служба {unit}: {status.active_state}.")
    else:
        parts.append(
            f"systemd недоступен в этой среде — убедитесь, что {unit} "
            "запущен на сервере."
        )
    return ReportToggleResult(
        enabled=True,
        message=" ".join(parts),
        scheduler=status,
        scheduler_action=action,
    )
