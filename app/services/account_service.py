"""Справочник УЗ на конечных точках и журнал появлений на устройствах.

Опрос (ping_service) передаёт сырое Win32_ComputerSystem.UserName.
Здесь нормализуем DOMAIN\\user, upsert в endpoint_accounts и пишем
device_account_history, если УЗ сменилась или прошло достаточно времени.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.authz import accessible_sector_ids
from app.extensions import db
from app.models import (
    Device,
    DeviceAccountHistory,
    EndpointAccount,
    SessionType,
)
from app.utils import utcnow

# Не плодим строку истории на каждый опрос одной и той же УЗ.
_SIGHTING_DEDUP = timedelta(hours=1)


def _as_utc(value: datetime) -> datetime:
    """SQLite часто отдаёт naive datetime — считаем его UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

# DOMAIN\user, .\user, user@upn, просто user.
_ACCOUNT_RE = re.compile(
    r"^(?:(?P<domain>[^\\/@]+)\\(?P<user>[^\\/@]+)"
    r"|(?P<upn_user>[^@\\/]+)@(?P<upn_domain>[^@\\/]+)"
    r"|(?P<local>[^\\/@]+))$"
)


def parse_windows_account(raw: str | None) -> tuple[str, str] | None:
    """Разбор Win32_ComputerSystem.UserName → (domain, username).

    domain в upper-case ('' для локальной УЗ), username в lower-case.
    None — пустое/мусорное значение (никто не залогинен).
    """
    if not raw:
        return None
    text = " ".join(str(raw).split()).strip()
    if not text or text in {".", "\\", "@"}:
        return None
    match = _ACCOUNT_RE.match(text)
    if match is None:
        return None
    if match.group("user"):
        domain = (match.group("domain") or "").strip()
        username = (match.group("user") or "").strip()
    elif match.group("upn_user"):
        domain = (match.group("upn_domain") or "").strip()
        username = (match.group("upn_user") or "").strip()
    else:
        domain = ""
        username = (match.group("local") or "").strip()
    if domain == ".":
        domain = ""
    username = username.lower()
    domain = domain.upper()
    if not username or len(username) > 128 or len(domain) > 128:
        return None
    return domain, username


def normalize_account_key(domain: str, username: str) -> str:
    if domain:
        return f"{domain}\\{username}"
    return username


def get_or_create_account(
    *,
    username: str,
    domain: str = "",
    display_name: str = "",
    seen_at=None,
) -> EndpointAccount:
    """Найти УЗ по (domain, username) или создать строку справочника."""
    username = username.strip().lower()
    domain = (domain or "").strip().upper()
    if domain == ".":
        domain = ""
    now = seen_at or utcnow()
    found = db.session.scalar(
        select(EndpointAccount).where(
            EndpointAccount.domain == domain,
            EndpointAccount.username == username,
        )
    )
    if found is not None:
        previous = found.last_seen_at
        if previous is None or _as_utc(now) > _as_utc(previous):
            found.last_seen_at = now
        if display_name and not found.display_name:
            found.display_name = display_name
        return found
    account = EndpointAccount(
        username=username,
        domain=domain,
        display_name=display_name or "",
        first_seen_at=now,
        last_seen_at=now,
    )
    db.session.add(account)
    db.session.flush()
    return account


def apply_logged_on_user(
    device: Device,
    raw_value: str | None,
    *,
    session_type: str = SessionType.INTERACTIVE,
    seen_at=None,
) -> EndpointAccount | None:
    """Обновить текущую УЗ устройства и при необходимости дописать историю.

    Вызывать внутри уже открытой транзакции опроса (до commit).
    """
    now = seen_at or utcnow()
    parsed = parse_windows_account(raw_value)
    if parsed is None:
        device.current_account_id = None
        device.current_account_seen_at = None
        return None

    domain, username = parsed
    account = get_or_create_account(username=username, domain=domain, seen_at=now)
    previous_id = device.current_account_id
    device.current_account_id = account.id
    device.current_account_seen_at = now

    should_write = previous_id != account.id
    if not should_write:
        last = db.session.scalar(
            select(DeviceAccountHistory)
            .where(
                DeviceAccountHistory.device_id == device.id,
                DeviceAccountHistory.account_id == account.id,
            )
            .order_by(DeviceAccountHistory.seen_at.desc(), DeviceAccountHistory.id.desc())
            .limit(1)
        )
        if last is None or (_as_utc(now) - _as_utc(last.seen_at)) >= _SIGHTING_DEDUP:
            should_write = True

    if should_write and device.id is not None:
        db.session.add(
            DeviceAccountHistory(
                device_id=device.id,
                account_id=account.id,
                seen_at=now,
                session_type=session_type,
                raw_value=(raw_value or "")[:255],
            )
        )
    return account


def list_visible_accounts(user, query: str = "", limit: int = 100) -> list[EndpointAccount]:
    """УЗ, которые встречались на доступных пользователю устройствах."""
    text = (query or "").strip()
    stmt = select(EndpointAccount).order_by(
        EndpointAccount.domain, EndpointAccount.username
    )
    if not user.is_admin:
        sector_ids = accessible_sector_ids(user)
        if not sector_ids:
            return []
        stmt = (
            stmt.join(DeviceAccountHistory, DeviceAccountHistory.account_id == EndpointAccount.id)
            .join(Device, Device.id == DeviceAccountHistory.device_id)
            .where(Device.sector_id.in_(sector_ids))
            .distinct()
        )
    if text:
        pattern = f"%{text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"
        stmt = stmt.where(
            or_(
                EndpointAccount.username.ilike(pattern, escape="\\"),
                EndpointAccount.domain.ilike(pattern, escape="\\"),
                EndpointAccount.display_name.ilike(pattern, escape="\\"),
            )
        )
    return list(db.session.scalars(stmt.limit(limit)).all())


def get_visible_account_or_404(user, account_id: int) -> EndpointAccount:
    from flask import abort

    account = db.session.get(EndpointAccount, account_id)
    if account is None:
        abort(404)
    if user.is_admin:
        return account
    sector_ids = accessible_sector_ids(user)
    if not sector_ids:
        abort(403)
    seen = db.session.scalar(
        select(DeviceAccountHistory.id)
        .join(Device, Device.id == DeviceAccountHistory.device_id)
        .where(
            DeviceAccountHistory.account_id == account.id,
            Device.sector_id.in_(sector_ids),
        )
        .limit(1)
    )
    if seen is None:
        abort(403)
    return account


def account_device_sightings(
    account: EndpointAccount,
    user,
    *,
    limit: int = 50,
) -> list[DeviceAccountHistory]:
    """История появлений УЗ на устройствах, видимых пользователю."""
    stmt = (
        select(DeviceAccountHistory)
        .options(
            selectinload(DeviceAccountHistory.device).selectinload(Device.sector),
        )
        .where(DeviceAccountHistory.account_id == account.id)
        .order_by(DeviceAccountHistory.seen_at.desc(), DeviceAccountHistory.id.desc())
    )
    if not user.is_admin:
        sector_ids = accessible_sector_ids(user)
        if not sector_ids:
            return []
        stmt = stmt.join(Device, Device.id == DeviceAccountHistory.device_id).where(
            Device.sector_id.in_(sector_ids)
        )
    return list(db.session.scalars(stmt.limit(limit)).all())


def account_current_devices(account: EndpointAccount, user) -> list[Device]:
    """Устройства, где эта УЗ сейчас current_account."""
    stmt = (
        select(Device)
        .options(selectinload(Device.sector))
        .where(Device.current_account_id == account.id)
        .order_by(Device.hostname, Device.ip)
    )
    if not user.is_admin:
        sector_ids = accessible_sector_ids(user)
        if not sector_ids:
            return []
        stmt = stmt.where(Device.sector_id.in_(sector_ids))
    return list(db.session.scalars(stmt).all())


def device_account_sightings(device_id: int, *, limit: int = 50) -> list[DeviceAccountHistory]:
    return list(
        db.session.scalars(
            select(DeviceAccountHistory)
            .options(selectinload(DeviceAccountHistory.account))
            .where(DeviceAccountHistory.device_id == device_id)
            .order_by(DeviceAccountHistory.seen_at.desc(), DeviceAccountHistory.id.desc())
            .limit(limit)
        ).all()
    )
