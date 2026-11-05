"""Пачки запусков: bulk ping/script и агрегация статуса по batch_id.

Маршруты только вызывают эти хелперы — без прямого ping/PsExec.
Вошедший видит все запуски пачки.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Device, RunStatus, Script, ScriptRun
from app.run_display import run_launch_label, run_type_label
from app.services.script_service import start_ping_on_devices, start_script_on_devices

MAX_BULK_DEVICES = 500
RUN_PAGE_PREFIX = "/admin/scripts/runs"


def run_page_path(run_id: int) -> str:
    """Канонический адрес журнала запуска. Старый /scripts/runs/… редиректит сюда."""
    return f"{RUN_PAGE_PREFIX}/{int(run_id)}"

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
    return rows


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
                "url": run_page_path(run.id),
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


@dataclass(frozen=True)
class BatchSummary:
    """Одна пачка для списка: когда началась, чем закончилась, как называется."""

    batch_id: str
    started_at: datetime | None
    total: int
    pending: int
    running: int
    success: int
    failed: int
    cancelled: int
    title: str
    run_type: str
    finished: bool


def list_batch_summaries(
    user,
    *,
    limit: int = 40,
    offset: int = 0,
    device_id: int | None = None,
    only_open: bool = False,
) -> tuple[list[BatchSummary], int]:
    """Пачки, которые пользователь может открыть. Новые сверху.

    device_id — только пачки, где есть запуск на этой карточке.
    only_open — есть хотя бы один pending или running.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return [], 0
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset or 0))
    filters = [ScriptRun.batch_id.is_not(None)]
    if device_id is not None:
        filters.append(ScriptRun.device_id == device_id)
    if only_open:
        filters.append(ScriptRun.status.in_((RunStatus.PENDING, RunStatus.RUNNING)))

    total = int(
        db.session.scalar(
            select(func.count(func.distinct(ScriptRun.batch_id))).where(*filters)
        )
        or 0
    )
    id_rows = db.session.execute(
        select(
            ScriptRun.batch_id,
            func.max(ScriptRun.started_at).label("started_at"),
        )
        .where(*filters)
        .group_by(ScriptRun.batch_id)
        .order_by(func.max(ScriptRun.started_at).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    batch_ids = [row.batch_id for row in id_rows if row.batch_id]
    if not batch_ids:
        return [], total

    runs = list(
        db.session.scalars(
            select(ScriptRun)
            .where(ScriptRun.batch_id.in_(batch_ids))
            .options(selectinload(ScriptRun.script))
            .order_by(ScriptRun.id)
        )
    )
    grouped: dict[str, list[ScriptRun]] = {batch_id: [] for batch_id in batch_ids}
    for run in runs:
        if run.batch_id in grouped:
            grouped[run.batch_id].append(run)

    summaries: list[BatchSummary] = []
    for batch_id in batch_ids:
        rows = grouped.get(batch_id) or []
        counts = {key: 0 for key in _STATUS_BUCKETS}
        for run in rows:
            if run.status in counts:
                counts[run.status] += 1
        head = rows[0] if rows else None
        title = "Пачка"
        run_type = ""
        if head is not None:
            run_type = head.run_type or ""
            if head.script is not None and head.script.name:
                title = head.script.name
            else:
                title = run_type_label(head.run_type)
                if len(rows) == 1:
                    title = run_launch_label(head)
        started = max((run.started_at for run in rows if run.started_at), default=None)
        summaries.append(
            BatchSummary(
                batch_id=batch_id,
                started_at=started,
                total=len(rows),
                pending=counts[RunStatus.PENDING],
                running=counts[RunStatus.RUNNING],
                success=counts[RunStatus.SUCCESS],
                failed=counts[RunStatus.FAILED],
                cancelled=counts[RunStatus.CANCELLED],
                title=title,
                run_type=run_type,
                finished=bool(rows)
                and all(run.status in RunStatus.FINISHED for run in rows),
            )
        )
    return summaries, total


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
