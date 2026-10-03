"""In-app уведомления: создание, лента, mark-read."""

from __future__ import annotations

from sqlalchemy import func, select, update

from app.extensions import db
from app.models.notification import Notification, NotificationKind
from app.models.script import RunStatus, RunType, ScriptRun
from app.utils import as_utc, utcnow


def create_notification(
    user_id: int,
    kind: str,
    title: str,
    body: str = "",
    link_url: str = "",
    *,
    commit: bool = True,
) -> Notification:
    row = Notification(
        user_id=user_id,
        kind=(kind or "").strip()[:32],
        title=(title or "").strip()[:255],
        body=body if body is not None else "",
        link_url=(link_url or "")[:512],
        created_at=utcnow(),
        read_at=None,
    )
    db.session.add(row)
    if commit:
        db.session.commit()
    return row


def serialize_notification(row: Notification) -> dict:
    created = as_utc(row.created_at) if row.created_at else None
    read = as_utc(row.read_at) if row.read_at else None
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "link_url": row.link_url,
        "created_at": created.isoformat() if created else None,
        "read_at": read.isoformat() if read else None,
        "unread": row.read_at is None,
    }


def list_for_user(user_id: int, *, limit: int = 50) -> dict:
    """Лента для GET /api/notifications: {items, unread}."""
    limit = max(1, min(int(limit or 50), 200))
    unread = int(
        db.session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id)
            .where(Notification.read_at.is_(None))
        )
        or 0
    )
    rows = list(
        db.session.scalars(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        ).all()
    )
    return {
        "items": [serialize_notification(row) for row in rows],
        "unread": unread,
    }


def mark_read(user_id: int, ids: list[int] | None = None) -> int:
    """Пометить прочитанными: конкретные id или все непрочитанные пользователя.

    Возвращает число обновлённых строк.
    """
    now = utcnow()
    stmt = (
        update(Notification)
        .where(Notification.user_id == user_id)
        .where(Notification.read_at.is_(None))
        .values(read_at=now)
    )
    if ids is not None:
        clean = []
        seen: set[int] = set()
        for raw in ids:
            try:
                number = int(raw)
            except (TypeError, ValueError):
                continue
            if number in seen:
                continue
            seen.add(number)
            clean.append(number)
        if not clean:
            return 0
        stmt = stmt.where(Notification.id.in_(clean))
    result = db.session.execute(stmt)
    db.session.commit()
    return int(result.rowcount or 0)


def device_offline_link(device_id: int) -> str:
    return f"/devices/{int(device_id)}"


def has_offline_alert_for_episode(
    user_id: int,
    device_id: int,
    last_seen,
) -> bool:
    """Уже есть device_offline за текущий offline-эпизод (после last_seen)."""
    link = device_offline_link(device_id)
    existing = existing_offline_alerts(
        [(user_id, link)],
    )
    created_times = existing.get((user_id, link), ())
    if last_seen is None:
        return bool(created_times)
    threshold = as_utc(last_seen)
    return any(created is not None and created >= threshold for created in created_times)


def existing_offline_alerts(
    pairs: list[tuple[int, str]],
) -> dict[tuple[int, str], list]:
    """Batch-lookup существующих device_offline по (user_id, link_url).

    Возвращает map (user_id, link_url) → список created_at (UTC).
    """
    if not pairs:
        return {}
    user_ids = {user_id for user_id, _ in pairs}
    links = {link for _, link in pairs}
    rows = db.session.execute(
        select(
            Notification.user_id,
            Notification.link_url,
            Notification.created_at,
        )
        .where(Notification.kind == NotificationKind.DEVICE_OFFLINE)
        .where(Notification.user_id.in_(user_ids))
        .where(Notification.link_url.in_(links))
    ).all()
    result: dict[tuple[int, str], list] = {}
    wanted = set(pairs)
    for user_id, link_url, created_at in rows:
        key = (int(user_id), link_url or "")
        if key not in wanted:
            continue
        result.setdefault(key, []).append(as_utc(created_at) if created_at else None)
    return result


def notify_script_failed(run: ScriptRun) -> Notification | None:
    """Опционально: уведомить автора скрипта о failed run."""
    if run is None or run.status != RunStatus.FAILED:
        return None
    if run.run_type not in {RunType.SCRIPT, RunType.COMMAND}:
        return None
    script = run.script
    if script is None or script.created_by_id is None:
        return None
    # Не дублировать: одно уведомление на script_run.
    link = f"/scripts/runs/{int(run.id)}"
    exists = db.session.scalar(
        select(Notification.id)
        .where(Notification.user_id == script.created_by_id)
        .where(Notification.kind == NotificationKind.SCRIPT_FAILED)
        .where(Notification.link_url == link)
        .limit(1)
    )
    if exists is not None:
        return None
    name = script.name or f"#{script.id}"
    host = ""
    if run.device is not None:
        host = run.device.hostname or run.device.ip or ""
    title = f"Скрипт «{name}» завершился с ошибкой"
    body = f"Запуск #{run.id}"
    if host:
        body = f"{body} на {host}"
    return create_notification(
        script.created_by_id,
        NotificationKind.SCRIPT_FAILED,
        title,
        body=body,
        link_url=link,
    )
