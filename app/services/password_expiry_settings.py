"""Ключи app_settings и чтение/запись настроек модуля паролей AD.

Не дублирует LDAP_*: сервер, base DN, bind и домен берутся из Flask config / .env.
Здесь только то, чего у bAWH ещё нет: пороги, OU-исключения, SMTP-получатели,
расписание и пауза рассылки.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from flask import current_app

from app.extensions import db
from app.models import AppSetting

PWD_MAX_AGE_DAYS = "pwd_max_age_days"
PWD_FIRST_WARNING_DAYS = "pwd_first_warning_days"
PWD_DAILY_WARNING_THRESHOLD = "pwd_daily_warning_threshold"
PWD_INSTRUCTIONS_URL = "pwd_instructions_url"
PWD_EXCLUDED_OU = "pwd_excluded_ou"
PWD_SEARCH_BASE = "pwd_search_base"
PWD_SCHEDULE_ENABLED = "pwd_schedule_enabled"
PWD_SCHEDULE_CRON = "pwd_schedule_cron"
PWD_PAUSE_USER_MAIL_UNTIL = "pwd_pause_user_mail_until"
PWD_ADMIN_RECIPIENTS = "pwd_admin_recipients"

DEFAULT_MAX_AGE_DAYS = 180
DEFAULT_FIRST_WARNING_DAYS = 5
DEFAULT_DAILY_WARNING_THRESHOLD = 3
DEFAULT_SCHEDULE_CRON = "0 8 * * *"


@dataclass(frozen=True)
class PasswordExpirySettings:
    """Параметры модуля поверх общего LDAP/.env."""

    max_pwd_age_days: int
    first_warning_days: int
    daily_warning_threshold: int
    instructions_url: str
    excluded_ou: list[str]
    search_base: str
    search_base_override: str
    schedule_enabled: bool
    schedule_cron: str
    pause_user_mail_until: date | None
    admin_recipients: list[str]


@dataclass(frozen=True)
class SmtpSettings:
    """SMTP из .env — отдельный канал, в bAWH раньше не было."""

    host: str
    port: int
    use_starttls: bool
    from_address: str
    username: str
    password: str

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)


def _get_raw(key: str) -> str | None:
    row = db.session.get(AppSetting, key)
    if row is None:
        return None
    return row.value


def _set_raw(key: str, value: str) -> None:
    row = db.session.get(AppSetting, key)
    if row is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def _split_list(raw: str, sep: str = ",") -> list[str]:
    return [item.strip() for item in (raw or "").split(sep) if item.strip()]


def _parse_optional_date(raw: str | None) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def get_smtp_settings() -> SmtpSettings:
    cfg = current_app.config
    host = str(cfg.get("SMTP_HOST") or "").strip()
    from_address = str(cfg.get("SMTP_FROM") or "").strip()
    username = str(cfg.get("SMTP_USER") or "").strip() or from_address
    password = str(cfg.get("SMTP_PASSWORD") or "")
    try:
        port = int(cfg.get("SMTP_PORT") or 25)
    except (TypeError, ValueError):
        port = 25
    use_starttls = bool(cfg.get("SMTP_USE_STARTTLS", False))
    return SmtpSettings(
        host=host,
        port=port,
        use_starttls=use_starttls,
        from_address=from_address,
        username=username,
        password=password,
    )


def get_password_expiry_settings() -> PasswordExpirySettings:
    """Собрать настройки модуля; search_base по умолчанию = LDAP_BASE_DN."""
    max_age = _int_setting(PWD_MAX_AGE_DAYS, DEFAULT_MAX_AGE_DAYS, minimum=1)
    first = _int_setting(PWD_FIRST_WARNING_DAYS, DEFAULT_FIRST_WARNING_DAYS, minimum=1)
    daily = _int_setting(
        PWD_DAILY_WARNING_THRESHOLD,
        DEFAULT_DAILY_WARNING_THRESHOLD,
        minimum=1,
    )
    if daily > first:
        daily = first

    search_override = (_get_raw(PWD_SEARCH_BASE) or "").strip()
    base_dn = str(current_app.config.get("LDAP_BASE_DN") or "").strip()
    search_base = search_override or base_dn

    cron = (_get_raw(PWD_SCHEDULE_CRON) or "").strip() or DEFAULT_SCHEDULE_CRON
    enabled_raw = (_get_raw(PWD_SCHEDULE_ENABLED) or "0").strip().lower()
    schedule_enabled = enabled_raw in {"1", "true", "yes", "on"}

    return PasswordExpirySettings(
        max_pwd_age_days=max_age,
        first_warning_days=first,
        daily_warning_threshold=daily,
        instructions_url=(_get_raw(PWD_INSTRUCTIONS_URL) or "").strip(),
        excluded_ou=_split_list(_get_raw(PWD_EXCLUDED_OU) or "", sep=";"),
        search_base=search_base,
        search_base_override=search_override,
        schedule_enabled=schedule_enabled,
        schedule_cron=cron,
        pause_user_mail_until=_parse_optional_date(_get_raw(PWD_PAUSE_USER_MAIL_UNTIL)),
        admin_recipients=_split_list(_get_raw(PWD_ADMIN_RECIPIENTS) or ""),
    )


def is_user_mail_paused(today: date | None = None) -> bool:
    until = get_password_expiry_settings().pause_user_mail_until
    if until is None:
        return False
    current = today or date.today()
    return current <= until


def set_password_expiry_settings(
    *,
    max_pwd_age_days: int | None = None,
    first_warning_days: int | None = None,
    daily_warning_threshold: int | None = None,
    instructions_url: str | None = None,
    excluded_ou: str | None = None,
    search_base: str | None = None,
    schedule_enabled: bool | None = None,
    schedule_cron: str | None = None,
    pause_user_mail_until: str | None = None,
    clear_pause: bool = False,
    admin_recipients: str | None = None,
) -> PasswordExpirySettings:
    """Обновить переданные поля. Пустой search_base сбрасывает override на LDAP_BASE_DN."""
    current = get_password_expiry_settings()

    if max_pwd_age_days is not None:
        value = int(max_pwd_age_days)
        if value < 1:
            raise ValueError("max_pwd_age_days должен быть ≥ 1")
        _set_raw(PWD_MAX_AGE_DAYS, str(value))

    if first_warning_days is not None:
        value = int(first_warning_days)
        if value < 1:
            raise ValueError("first_warning_days должен быть ≥ 1")
        _set_raw(PWD_FIRST_WARNING_DAYS, str(value))

    if daily_warning_threshold is not None:
        value = int(daily_warning_threshold)
        if value < 1:
            raise ValueError("daily_warning_threshold должен быть ≥ 1")
        first_eff = (
            int(first_warning_days)
            if first_warning_days is not None
            else current.first_warning_days
        )
        if value > first_eff:
            raise ValueError("daily_warning_threshold не может быть больше first_warning_days")
        _set_raw(PWD_DAILY_WARNING_THRESHOLD, str(value))

    if instructions_url is not None:
        _set_raw(PWD_INSTRUCTIONS_URL, instructions_url.strip())

    if excluded_ou is not None:
        _set_raw(PWD_EXCLUDED_OU, excluded_ou.strip())

    if search_base is not None:
        _set_raw(PWD_SEARCH_BASE, search_base.strip())

    if schedule_enabled is not None:
        _set_raw(PWD_SCHEDULE_ENABLED, "1" if schedule_enabled else "0")

    if schedule_cron is not None:
        cron = schedule_cron.strip() or DEFAULT_SCHEDULE_CRON
        _validate_cron(cron)
        _set_raw(PWD_SCHEDULE_CRON, cron)

    if clear_pause:
        _set_raw(PWD_PAUSE_USER_MAIL_UNTIL, "")
    elif pause_user_mail_until is not None:
        text = pause_user_mail_until.strip()
        if text:
            _parse_optional_date(text)  # validate
        _set_raw(PWD_PAUSE_USER_MAIL_UNTIL, text)

    if admin_recipients is not None:
        _set_raw(PWD_ADMIN_RECIPIENTS, admin_recipients.strip())

    db.session.commit()
    return get_password_expiry_settings()


def _int_setting(key: str, default: int, *, minimum: int) -> int:
    raw = _get_raw(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, value)


def _validate_cron(expr: str) -> None:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron должен быть из 5 полей: минута час день месяц день_недели")
