"""Включение/выключение расписания отчётов + ensure службы bawh-scheduler.

Тумблер в UI включает job в APScheduler (флаг в app_settings) и при
включении поднимает systemd-unit bawh-scheduler той же sudo-учёткой,
что и перезапуск после обновления.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.systemd_service import (
    SystemdError,
    UnitStatus,
    ensure_scheduler_running,
    get_unit_status,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportToggleResult:
    """Результат переключения тумблера рассылки."""

    enabled: bool
    message: str
    scheduler: UnitStatus
    scheduler_action: str = ""


def scheduler_status() -> UnitStatus:
    return get_unit_status()


def apply_report_toggle(
    *,
    enabled: bool,
    set_enabled,
    label: str,
) -> ReportToggleResult:
    """Сохранить флаг расписания и при включении поднять bawh-scheduler.

    set_enabled(bool) — колбэк модуля (password / sector report), пишет app_settings.
    Выключение тумблера не останавливает службу: опрос сети тоже в ней.
    """
    set_enabled(bool(enabled))
    status = scheduler_status()
    if not enabled:
        return ReportToggleResult(
            enabled=False,
            message=(
                f"{label}: рассылка выключена. Отчёты по расписанию не уходят. "
                f"Служба bawh-scheduler не останавливалась "
                f"(сейчас: {status.active_state})."
            ),
            scheduler=status,
        )

    action = ""
    try:
        ensure = ensure_scheduler_running()
        status = ensure.status
        action = ensure.message
    except SystemdError as exc:
        logger.exception("%s: не удалось поднять bawh-scheduler", label)
        return ReportToggleResult(
            enabled=True,
            message=(
                f"{label}: расписание включено, но службу bawh-scheduler "
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
        parts.append(f"Служба bawh-scheduler: {status.active_state}.")
    else:
        parts.append(
            "systemd недоступен в этой среде — убедитесь, что bawh-scheduler "
            "запущен на сервере."
        )
    return ReportToggleResult(
        enabled=True,
        message=" ".join(parts),
        scheduler=status,
        scheduler_action=action,
    )
