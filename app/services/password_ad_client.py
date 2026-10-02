"""LDAP-выборка пользователей AD для проверки срока пароля.

Host/base/domain — из .env (общие с входом). Bind-учётка — из настроек модуля
(app_settings) с fallback на LDAP_BIND_* в .env.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from flask import current_app
from ldap3 import BASE, NONE, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException

from app.services import ldap_service
from app.services.password_expiry_settings import (
    PasswordExpirySettings,
    get_ldap_bind_settings,
    get_password_expiry_settings,
)

logger = logging.getLogger(__name__)

UAC_ACCOUNTDISABLE = 0x0002
UAC_PASSWORD_NEVER_EXPIRES = 0x10000
WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

UserStatus = Literal["ok", "upcoming", "overdue", "must_change"]

LDAP_USER_FILTER = (
    "(&(objectCategory=person)(objectClass=user)"
    f"(!(userAccountControl:1.2.840.113556.1.4.803:={UAC_ACCOUNTDISABLE})))"
)
LDAP_ATTRIBUTES = [
    "sAMAccountName",
    "displayName",
    "cn",
    "mail",
    "pwdLastSet",
    "userAccountControl",
    "distinguishedName",
]


class PasswordAdError(RuntimeError):
    """Ошибка подключения или поиска в каталоге."""


@dataclass
class AdUser:
    """Пользователь AD с рассчитанным сроком пароля."""

    username: str
    email: str
    full_name: str
    distinguished_name: str
    user_account_control: int
    pwd_last_set: datetime | None
    pwd_last_set_snapshot: str
    days_left: int | None
    expiry_date: date | None
    status: UserStatus


def filetime_to_datetime(value: Any) -> datetime | None:
    if value in (None, 0, "0"):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return filetime_to_datetime(value[0])
    try:
        raw = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Некорректный pwdLastSet: {value!r}") from exc
    if raw == 0:
        return None
    return WINDOWS_EPOCH + timedelta(microseconds=raw / 10)


def snapshot_pwd_last_set(value: Any) -> str:
    if value in (None, 0, "0"):
        return "0"
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return str(int((dt.astimezone(timezone.utc) - WINDOWS_EPOCH).total_seconds() * 10_000_000))
    if isinstance(value, (list, tuple)):
        return snapshot_pwd_last_set(value[0] if value else 0)
    return str(int(value))


def compute_days_left(
    pwd_last_set: datetime | None,
    max_pwd_age_days: int,
    today: date | None = None,
) -> tuple[int | None, date | None]:
    if pwd_last_set is None:
        return None, None
    current = today or date.today()
    expiry = pwd_last_set.date() + timedelta(days=max_pwd_age_days)
    return (expiry - current).days, expiry


def classify_user(
    days_left: int | None,
    first_warning_days: int,
    *,
    must_change: bool = False,
) -> UserStatus:
    if must_change or days_left is None:
        return "must_change"
    if days_left <= 0:
        return "overdue"
    if days_left <= first_warning_days:
        return "upcoming"
    return "ok"


def _first_value(values: Any, default: Any = None) -> Any:
    if values in (None, "", []):
        return default
    if isinstance(values, (list, tuple)):
        return values[0] if values else default
    return values


def _is_excluded(dn: str, excluded_ou: list[str]) -> bool:
    dn_upper = dn.upper()
    return any(fragment.upper() in dn_upper for fragment in excluded_ou if fragment)


def _open_service_connection() -> Connection:
    """Bind сервисной учёткой модуля (UI) или LDAP_BIND_* из .env."""
    cfg = current_app.config
    ldap = get_ldap_bind_settings()
    host = ldap.host
    if not host:
        raise PasswordAdError("LDAP_HOST пуст — укажите контроллер домена в .env")

    if not ldap.configured:
        raise PasswordAdError(
            "Для чтения каталога укажите DN и пароль сервисной учётки "
            "в Настройки → Пароли AD (или LDAP_BIND_DN / LDAP_BIND_PASSWORD в .env)"
        )

    try:
        port = int(cfg.get("LDAP_PORT") or 636)
    except (TypeError, ValueError):
        port = 636
    use_ssl = ldap_service.as_bool(cfg.get("LDAP_USE_SSL", True), default=True)
    host, port, use_ssl = ldap_service.ldap_endpoint(host, port, use_ssl)

    server = Server(host, port=port, use_ssl=use_ssl, get_info=NONE, connect_timeout=10)
    try:
        return Connection(
            server,
            user=ldap.bind_dn,
            password=ldap.bind_password,
            auto_bind=True,
            receive_timeout=30,
            raise_exceptions=True,
        )
    except (LDAPException, OSError, ValueError) as exc:
        raise PasswordAdError(f"Не удалось подключиться к LDAP: {exc}") from exc


def fetch_password_users(
    settings: PasswordExpirySettings | None = None,
    today: date | None = None,
) -> list[AdUser]:
    """Выгрузить активных пользователей и рассчитать срок пароля."""
    settings = settings or get_password_expiry_settings()
    if not settings.search_base:
        raise PasswordAdError(
            "Не задан search_base: укажите LDAP_BASE_DN в .env или override в настройках модуля"
        )

    connection = _open_service_connection()
    try:
        try:
            entries = connection.extend.standard.paged_search(
                search_base=settings.search_base,
                search_filter=LDAP_USER_FILTER,
                search_scope=SUBTREE,
                attributes=LDAP_ATTRIBUTES,
                paged_size=500,
                generator=False,
            )
        except LDAPException as exc:
            raise PasswordAdError(f"Ошибка LDAP-поиска: {exc}") from exc

        users: list[AdUser] = []
        skipped_no_mail = 0
        skipped_never_expires = 0
        skipped_excluded = 0

        for entry in entries:
            if entry.get("type") not in (None, "searchResEntry"):
                continue
            attrs = entry.get("attributes") or {}
            dn = str(entry.get("dn") or "")
            username = str(_first_value(attrs.get("sAMAccountName"), "") or "")
            if _is_excluded(dn, settings.excluded_ou):
                skipped_excluded += 1
                continue

            uac = int(_first_value(attrs.get("userAccountControl"), 0) or 0)
            if uac & UAC_PASSWORD_NEVER_EXPIRES:
                skipped_never_expires += 1
                continue

            mail = str(_first_value(attrs.get("mail"), "") or "").strip()
            if not mail:
                skipped_no_mail += 1
                continue

            raw_pwd = _first_value(attrs.get("pwdLastSet"), 0)
            pwd_dt = filetime_to_datetime(raw_pwd)
            days_left, expiry = compute_days_left(
                pwd_dt, settings.max_pwd_age_days, today=today
            )
            status = classify_user(
                days_left,
                settings.first_warning_days,
                must_change=pwd_dt is None,
            )
            full_name = str(
                _first_value(attrs.get("displayName"))
                or _first_value(attrs.get("cn"))
                or username
            )
            users.append(
                AdUser(
                    username=username,
                    email=mail,
                    full_name=full_name,
                    distinguished_name=dn,
                    user_account_control=uac,
                    pwd_last_set=pwd_dt,
                    pwd_last_set_snapshot=snapshot_pwd_last_set(raw_pwd),
                    days_left=days_left,
                    expiry_date=expiry,
                    status=status,
                )
            )

        logger.info(
            "Пароли AD: обработано %s (без email: %s, never-expires: %s, OU: %s)",
            len(users),
            skipped_no_mail,
            skipped_never_expires,
            skipped_excluded,
        )
        return users
    finally:
        ldap_service.unbind(connection)


def verify_password_directory_access(
    settings: PasswordExpirySettings | None = None,
) -> str:
    """Проверить bind и доступ к search_base (BASE-поиск)."""
    settings = settings or get_password_expiry_settings()
    if not settings.search_base:
        raise PasswordAdError("Не задан search_base / LDAP_BASE_DN")

    connection = _open_service_connection()
    try:
        try:
            ok = connection.search(
                search_base=settings.search_base,
                search_filter="(objectClass=*)",
                search_scope=BASE,
                attributes=["distinguishedName"],
                size_limit=1,
            )
        except LDAPException as exc:
            raise PasswordAdError(
                f"Нет доступа к search_base «{settings.search_base}»: {exc}"
            ) from exc
        if not ok or not connection.entries:
            raise PasswordAdError(
                f"Нет доступа к search_base «{settings.search_base}»: "
                "объект не найден или недостаточно прав"
            )
        dn = str(connection.entries[0].entry_dn)
        host = str(current_app.config.get("LDAP_HOST") or "")
        return f"LDAP OK: {host}, search_base={dn}"
    finally:
        ldap_service.unbind(connection)
