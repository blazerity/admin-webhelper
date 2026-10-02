"""Сводка сети для карты и health планировщика опроса.

Агрегаты по видимым устройствам (accessible_sector_ids) + последний
прогон network_poll_runs. Маршруты только читают dict — без ping/LDAP.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.authz import accessible_sector_ids
from app.extensions import db
from app.models import Device, DeviceStatus, NetworkPollRun, RunStatus, ScriptRun
from app.services.settings_service import get_poll_interval_seconds
from app.utils import as_utc, utcnow

# Нижняя граница «устаревания» успешного опроса, даже при коротком интервале.
_MIN_STALE_AFTER_SECONDS = 600


def _iso(value) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()


def poll_run_to_dict(run: NetworkPollRun | None) -> dict | None:
    """Сериализация network_poll_runs в контракт last_poll / last_run."""
    if run is None:
        return None
    return {
        "id": run.id,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "mode": run.mode or "scheduled",
        "scanned": int(run.scanned or 0),
        "online": int(run.online or 0),
        "offline": int(run.offline or 0),
        "errors": int(run.errors or 0),
        "error": run.error or "",
    }


def latest_poll_run() -> NetworkPollRun | None:
    """Глобальный последний прогон опроса (новые сверху)."""
    return db.session.scalars(
        select(NetworkPollRun).order_by(NetworkPollRun.id.desc()).limit(1)
    ).first()


def latest_successful_poll_run() -> NetworkPollRun | None:
    """Последний завершённый прогон без текстовой ошибки."""
    return db.session.scalars(
        select(NetworkPollRun)
        .where(NetworkPollRun.finished_at.is_not(None))
        .where(NetworkPollRun.error == "")
        .order_by(NetworkPollRun.finished_at.desc(), NetworkPollRun.id.desc())
        .limit(1)
    ).first()


def _device_status_counts(user) -> dict[str, int]:
    """Счётчики устройств по last_status в доступных секторах."""
    counts = {
        "devices_total": 0,
        "devices_online": 0,
        "devices_offline": 0,
        "devices_unknown": 0,
    }
    if user.is_admin:
        sector_ids = None
    else:
        sector_ids = accessible_sector_ids(user)
        if not sector_ids:
            return counts

    stmt = select(Device.last_status, func.count()).group_by(Device.last_status)
    if sector_ids is not None:
        stmt = stmt.where(Device.sector_id.in_(sector_ids))

    for status, n in db.session.execute(stmt).all():
        n = int(n)
        counts["devices_total"] += n
        if status == DeviceStatus.ONLINE:
            counts["devices_online"] += n
        elif status == DeviceStatus.OFFLINE:
            counts["devices_offline"] += n
        else:
            counts["devices_unknown"] += n
    return counts


def _failed_script_runs_24h(user) -> int:
    """Число failed script_runs за сутки с учётом видимости устройств."""
    since = utcnow() - timedelta(hours=24)
    stmt = (
        select(func.count())
        .select_from(ScriptRun)
        .where(ScriptRun.status == RunStatus.FAILED)
        .where(ScriptRun.started_at >= since)
    )
    if not user.is_admin:
        sector_ids = accessible_sector_ids(user)
        if not sector_ids:
            return 0
        stmt = stmt.join(Device, Device.id == ScriptRun.device_id).where(
            Device.sector_id.in_(sector_ids)
        )
    return int(db.session.scalar(stmt) or 0)


def get_network_summary(user) -> dict:
    """Сводка для GET /api/network/summary."""
    counts = _device_status_counts(user)
    return {
        **counts,
        "last_poll": poll_run_to_dict(latest_poll_run()),
        "failed_script_runs_24h": _failed_script_runs_24h(user),
    }


def stale_after_seconds(poll_interval: int | None = None) -> int:
    """Порог устаревания: max(poll_interval * 2, 600)."""
    if poll_interval is None:
        poll_interval = get_poll_interval_seconds()
    return max(int(poll_interval) * 2, _MIN_STALE_AFTER_SECONDS)


def get_scheduler_health() -> dict:
    """Состояние опроса для блока на /admin/settings."""
    last_success = latest_successful_poll_run()
    last_run = latest_poll_run()
    threshold = stale_after_seconds()
    last_success_at = last_success.finished_at if last_success is not None else None
    if last_success_at is None:
        stale = True
    else:
        age = (utcnow() - as_utc(last_success_at)).total_seconds()
        stale = age > threshold
    return {
        "last_success_at": _iso(last_success_at),
        "last_run": poll_run_to_dict(last_run),
        "stale": stale,
        "stale_after_seconds": threshold,
    }
