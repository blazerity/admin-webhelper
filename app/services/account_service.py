"""Справочник УЗ на конечных точках и журнал появлений на устройствах.

Опрос передаёт сырое Win32_ComputerSystem.UserName. Здесь нормализуем
DOMAIN\\user, upsert в endpoint_accounts и пишем device_account_history,
если УЗ сменилась или прошло достаточно времени с прошлого факта.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.authz import accessible_sector_ids
from app.extensions import db
from app.models import Device, DeviceAccountHistory, EndpointAccount, SessionType
from app.utils import as_utc, ilike_pattern, utcnow

logger = logging.getLogger(__name__)

# Не плодим строку истории на каждый опрос одной и той же УЗ.
_SIGHTING_DEDUP = timedelta(hours=1)

# DOMAIN\user, .\user, user@upn, просто user.
_ACCOUNT_RE = re.compile(
    r"^(?:(?P<domain>[^\\/@]+)\\(?P<user>[^\\/@]+)"
    r"|(?P<upn_user>[^@\\/]+)@(?P<upn_domain>[^@\\/]+)"
    r"|(?P<local>[^\\/@]+))$"
)


def normalize_domain(domain: str | None) -> str:
    """Ключ домена: upper-case NetBIOS (первая метка DNS/UPN).

    CORP\\alice и alice@corp.local → один и тот же domain «CORP».
    Пустая строка или «.» — локальная УЗ.
    """
    text = (domain or "").strip().upper()
    if not text or text == ".":
        return ""
    return text.split(".", 1)[0][:128]


def normalize_username(username: str | None) -> str:
    return (username or "").strip().lower()[:128]


def parse_windows_account(raw: str | None) -> tuple[str, str] | None:
    """Разбор Win32_ComputerSystem.UserName → (domain, username).

    domain — NetBIOS upper-case ('' для локальной), username — lower-case.
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
        domain = match.group("domain") or ""
        username = match.group("user") or ""
    elif match.group("upn_user"):
        domain = match.group("upn_domain") or ""
        username = match.group("upn_user") or ""
    else:
        domain = ""
        username = match.group("local") or ""
    domain = normalize_domain(domain)
    username = normalize_username(username)
    if not username:
        return None
    return domain, username


def normalize_account_key(domain: str, username: str) -> str:
    domain = normalize_domain(domain)
    username = normalize_username(username)
    if domain:
        return f"{domain}\\{username}"
    return username


def _find_account(domain: str, username: str) -> EndpointAccount | None:
    return db.session.scalar(
        select(EndpointAccount).where(
            EndpointAccount.domain == domain,
            EndpointAccount.username == username,
        )
    )


def get_or_create_account(
    *,
    username: str,
    domain: str = "",
    display_name: str = "",
    seen_at=None,
) -> EndpointAccount:
    """Найти УЗ по (domain, username) или создать. Устойчив к гонке insert."""
    username = normalize_username(username)
    domain = normalize_domain(domain)
    if not username:
        raise ValueError("username пуст")
    now = seen_at or utcnow()

    found = _find_account(domain, username)
    if found is not None:
        return _touch_account(found, now=now, display_name=display_name)

    account = EndpointAccount(
        username=username,
        domain=domain,
        display_name=display_name or "",
        first_seen_at=now,
        last_seen_at=now,
    )
    try:
        # SAVEPOINT: гонка unique не откатывает внешнюю транзакцию опроса.
        with db.session.begin_nested():
            db.session.add(account)
            db.session.flush()
        return account
    except IntegrityError:
        found = _find_account(domain, username)
        if found is None:
            logger.exception(
                "Не удалось создать endpoint_account %s\\%s после IntegrityError",
                domain,
                username,
            )
            raise
        return _touch_account(found, now=now, display_name=display_name)


def _touch_account(
    account: EndpointAccount,
    *,
    now,
    display_name: str = "",
) -> EndpointAccount:
    previous = account.last_seen_at
    if previous is None or as_utc(now) > as_utc(previous):
        account.last_seen_at = now
    if display_name and not account.display_name:
        account.display_name = display_name
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
        if last is None or (as_utc(now) - as_utc(last.seen_at)) >= _SIGHTING_DEDUP:
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


def list_visible_accounts(
    user,
    *,
    q: str = "",
    page: int = 1,
    per_page: int = 50,
    limit: int | None = None,
) -> dict:
    """УЗ на доступных устройствах. ``{items, total, page, per_page}``.

    ``limit`` — совместимость (page=1, per_page=limit).
    """
    from app.utils import normalize_page

    text = (q or "").strip()
    if limit is not None:
        page, per_page = 1, max(1, int(limit))
    page, per_page = normalize_page(page, per_page)

    empty = {"items": [], "total": 0, "page": page, "per_page": per_page}
    stmt = select(EndpointAccount)
    if not user.is_admin:
        sector_ids = accessible_sector_ids(user)
        if not sector_ids:
            return empty
        stmt = (
            stmt.join(DeviceAccountHistory, DeviceAccountHistory.account_id == EndpointAccount.id)
            .join(Device, Device.id == DeviceAccountHistory.device_id)
            .where(Device.sector_id.in_(sector_ids))
            .distinct()
        )
    if text:
        pattern = ilike_pattern(text)
        stmt = stmt.where(
            or_(
                EndpointAccount.username.ilike(pattern, escape="\\"),
                EndpointAccount.domain.ilike(pattern, escape="\\"),
                EndpointAccount.display_name.ilike(pattern, escape="\\"),
            )
        )

    # distinct + count: считаем id после фильтрации.
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(db.session.scalar(count_stmt) or 0)
    rows = list(
        db.session.scalars(
            stmt.order_by(EndpointAccount.domain, EndpointAccount.username)
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()
    )
    return {
        "items": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


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


def count_device_account_sightings(device_id: int) -> int:
    return int(
        db.session.scalar(
            select(func.count())
            .select_from(DeviceAccountHistory)
            .where(DeviceAccountHistory.device_id == device_id)
        )
        or 0
    )


def device_account_sightings(
    device_id: int, *, limit: int = 50, offset: int = 0
) -> list[DeviceAccountHistory]:
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset or 0))
    return list(
        db.session.scalars(
            select(DeviceAccountHistory)
            .options(selectinload(DeviceAccountHistory.account))
            .where(DeviceAccountHistory.device_id == device_id)
            .order_by(DeviceAccountHistory.seen_at.desc(), DeviceAccountHistory.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
