"""Watchlist устройств и offline-алерты после опроса сети."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.device import Device, DeviceStatus
from app.models.notification import NotificationKind
from app.models.watchlist import DeviceWatchlist
from app.services import notification_service
from app.utils import as_utc, parse_optional_int, utcnow

logger = logging.getLogger(__name__)

DEFAULT_OFFLINE_MINUTES = 15
_MIN_OFFLINE_MINUTES = 1
_MAX_OFFLINE_MINUTES = 7 * 24 * 60  # неделя


def normalize_offline_minutes(raw) -> int:
    """Число минут порога; битое/пустое → default 15."""
    if isinstance(raw, bool):
        return DEFAULT_OFFLINE_MINUTES
    if isinstance(raw, int):
        value = raw
    else:
        value = parse_optional_int(str(raw) if raw is not None else None)
    if value is None:
        return DEFAULT_OFFLINE_MINUTES
    return max(_MIN_OFFLINE_MINUTES, min(int(value), _MAX_OFFLINE_MINUTES))


def get_watch(user_id: int, device_id: int) -> DeviceWatchlist | None:
    return db.session.scalar(
        select(DeviceWatchlist)
        .where(DeviceWatchlist.user_id == user_id)
        .where(DeviceWatchlist.device_id == device_id)
    )


def is_watching(user_id: int, device_id: int) -> bool:
    return get_watch(user_id, device_id) is not None


def watch(user_id: int, device_id: int, offline_minutes=None) -> DeviceWatchlist:
    """Добавить или обновить подписку (UNIQUE user+device)."""
    minutes = normalize_offline_minutes(offline_minutes)
    row = get_watch(user_id, device_id)
    if row is None:
        row = DeviceWatchlist(
            user_id=user_id,
            device_id=device_id,
            offline_minutes=minutes,
            created_at=utcnow(),
        )
        db.session.add(row)
    else:
        row.offline_minutes = minutes
    db.session.commit()
    return row


def unwatch(user_id: int, device_id: int) -> bool:
    """Снять подписку. True если строка была."""
    row = get_watch(user_id, device_id)
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def offline_duration_minutes(device: Device, now: datetime | None = None) -> float | None:
    """Сколько минут устройство offline; None если не offline."""
    if device.last_status != DeviceStatus.OFFLINE:
        return None
    moment = as_utc(now or utcnow())
    if device.last_seen is not None:
        start = as_utc(device.last_seen)
    else:
        start = as_utc(device.created_at)
    return max(0.0, (moment - start).total_seconds() / 60.0)


def evaluate_watchlist_alerts(*, now: datetime | None = None) -> int:
    """После poll: создать deduped device_offline уведомления.

    Возвращает число созданных уведомлений.
    """
    moment = as_utc(now or utcnow())
    rows = list(
        db.session.scalars(
            select(DeviceWatchlist).options(
                selectinload(DeviceWatchlist.device),
            )
        ).all()
    )
    created = 0
    for entry in rows:
        device = entry.device
        if device is None:
            continue
        duration = offline_duration_minutes(device, moment)
        if duration is None:
            continue
        if duration < float(entry.offline_minutes or DEFAULT_OFFLINE_MINUTES):
            continue
        if notification_service.has_offline_alert_for_episode(
            entry.user_id,
            device.id,
            device.last_seen,
        ):
            continue
        label = device.hostname or device.ip or f"#{device.id}"
        minutes = int(entry.offline_minutes or DEFAULT_OFFLINE_MINUTES)
        notification_service.create_notification(
            entry.user_id,
            NotificationKind.DEVICE_OFFLINE,
            f"Устройство {label} offline",
            body=f"Не отвечает дольше {minutes} мин.",
            link_url=notification_service.device_offline_link(device.id),
            commit=False,
        )
        created += 1
    if created:
        db.session.commit()
    return created
