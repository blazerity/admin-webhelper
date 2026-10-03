"""Пачки запусков: bulk ping/script и агрегация статуса по batch_id.

Маршруты только вызывают эти хелперы — без прямого ping/PsExec.
Видимость runs фильтруется через sector ACL / author (authz).
"""

from __future__ import annotations

from sqlalchemy.orm import selectinload

from app.authz import accessible_sector_ids
from app.models import Device, RunStatus, Script, ScriptRun
from app.services.script_service import start_ping_on_devices, start_script_on_devices

MAX_BULK_DEVICES = 100

_STATUS_BUCKETS = (
    RunStatus.PENDING,
    RunStatus.RUNNING,
    RunStatus.SUCCESS,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
)


def list_visible_batch_runs(user, batch_id: str) -> list[ScriptRun]:
    """Все ScriptRun с данным batch_id, видимые пользователю (по id)."""
    if not batch_id or user is None or not getattr(user, "is_authenticated", False):
        return []
    rows = (
        ScriptRun.query.options(selectinload(ScriptRun.device))
        .filter(ScriptRun.batch_id == batch_id)
        .order_by(ScriptRun.id)
        .all()
    )
    if not rows:
        return []
    if getattr(user, "is_admin", False):
        return rows

    # Один ACL-запрос секторов на пачку (вместо N× в user_can_access_device).
    sector_ids = accessible_sector_ids(user)
    visible: list[ScriptRun] = []
    for run in rows:
        if run.user_id is not None and run.user_id == getattr(user, "id", None):
            visible.append(run)
            continue
        device = run.device
        if (
            device is not None
            and device.sector_id is not None
            and device.sector_id in sector_ids
        ):
            visible.append(run)
    return visible


def batch_status_payload(user, batch_id: str) -> dict | None:
    """Сводка batch для GET /api/batches/<id>. None — нет видимых runs (→ 404)."""
    runs = list_visible_batch_runs(user, batch_id)
    if not runs:
        return None

    counts = {key: 0 for key in _STATUS_BUCKETS}
    items: list[dict] = []
    for run in runs:
        status = run.status or ""
        if status in counts:
            counts[status] += 1
        device = run.device
        items.append(
            {
                "id": run.id,
                "device_id": run.device_id,
                "hostname": (device.hostname if device is not None else None) or "",
                "ip": (device.ip if device is not None else None) or "",
                "status": status,
                "run_type": run.run_type,
                # Относительный путь: сервис вызывается и вне request context.
                "url": f"/scripts/runs/{run.id}",
            }
        )

    return {
        "batch_id": batch_id,
        "total": len(runs),
        "pending": counts[RunStatus.PENDING],
        "running": counts[RunStatus.RUNNING],
        "success": counts[RunStatus.SUCCESS],
        "failed": counts[RunStatus.FAILED],
        "cancelled": counts[RunStatus.CANCELLED],
        "finished": all(run.status in RunStatus.FINISHED for run in runs),
        "runs": items,
    }


def start_bulk_ping(user, devices: list[Device]) -> tuple[str, list[ScriptRun]]:
    """Один batch_id + один commit; воркеры — из очереди script_service."""
    return start_ping_on_devices(user, devices)


def start_bulk_script(
    script: Script, user, devices: list[Device]
) -> tuple[str, list[ScriptRun]]:
    """Обёртка над start_script_on_devices: возвращает (batch_id, runs)."""
    runs = start_script_on_devices(script, user, devices)
    if not runs:
        return "", []
    return runs[0].batch_id or "", runs
