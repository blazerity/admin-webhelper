"""Журнал прогона: одна проверка «уже идёт / завис» для разных таблиц.

Опрос сети и опрос железа писали её дважды. У строки должны быть
колонки id, started_at, finished_at и error.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.utils import as_utc


def unfinished_run(model, now: datetime, stale_after: timedelta):
    """Незавершённый прогон младше порога устаревания."""
    cutoff = as_utc(now) - stale_after
    return db.session.scalars(
        select(model)
        .where(model.finished_at.is_(None), model.started_at >= cutoff)
        .order_by(model.id.desc())
        .limit(1)
    ).first()


def close_stale_runs(
    model,
    now: datetime,
    stale_after: timedelta,
    *,
    message: str,
) -> None:
    """Пометить зависшие прогоны (процесс убит) как завершённые с ошибкой."""
    cutoff = as_utc(now) - stale_after
    stale = list(
        db.session.scalars(
            select(model).where(
                model.finished_at.is_(None),
                model.started_at < cutoff,
            )
        )
    )
    if not stale:
        return
    for row in stale:
        row.finished_at = now
        row.error = row.error or message
    db.session.commit()


def count_rows(model) -> int:
    """Число строк модели. Пустая таблица — 0, не None."""
    return int(db.session.scalar(select(func.count()).select_from(model)) or 0)


def load_recent_by_id(model, limit: int, *, offset: int = 0, cap: int = 200):
    """Последние строки по id, новые сверху. limit режется до cap."""
    bounded = max(1, min(cap, int(limit)))
    start = max(0, int(offset or 0))
    return list(
        db.session.scalars(
            select(model).order_by(model.id.desc()).offset(start).limit(bounded)
        )
    )
