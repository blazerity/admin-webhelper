"""Ключи app_settings и чтение/запись настроек модуля паролей AD.

Сервер LDAP (host/base/domain) по-прежнему из Flask config / .env — общий с входом.
Учётка bind для чтения каталога, SMTP и пороги модуля — в app_settings (пароли Fernet).
Если UI-поля пусты, берётся fallback из .env (SMTP_*, LDAP_BIND_*).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from flask import current_app

from app.extensions import db
from app.services.crypto_service import CryptoError, CryptoNotConfigured, decrypt, encrypt
from app.utils import as_truthy

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

PWD_LDAP_BIND_DN = "pwd_ldap_bind_dn"
PWD_LDAP_BIND_PASSWORD = "pwd_ldap_bind_password"

PWD_SMTP_HOST = "pwd_smtp_host"
PWD_SMTP_PORT = "pwd_smtp_port"
PWD_SMTP_USE_STARTTLS = "pwd_smtp_use_starttls"
PWD_SMTP_FROM = "pwd_smtp_from"
PWD_SMTP_USER = "pwd_smtp_user"
PWD_SMTP_PASSWORD = "pwd_smtp_password"

DEFAULT_MAX_AGE_DAYS = 180
DEFAULT_FIRST_WARNING_DAYS = 5
DEFAULT_DAILY_WARNING_THRESHOLD = 3
DEFAULT_SCHEDULE_CRON = "0 8 * * *"


@dataclass(frozen=True)
class PasswordExpirySettings:
    """Параметры модуля поверх общего LDAP host/base/domain."""

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
class LdapBindSettings:
    """Учётка bind для модуля: UI override или LDAP_BIND_* из .env."""

    host: str
    base_dn: str
    domain: str
    bind_dn: str
    bind_password: str
    bind_dn_override: str
    has_bind_password: bool
    source: str  # ui | env | none

    @property
    def configured(self) -> bool:
        return bool(self.bind_dn and self.bind_password)


@dataclass(frozen=True)
class SmtpSettings:
    """SMTP: UI override или SMTP_* из .env."""

    host: str
    port: int
    use_starttls: bool
    from_address: str
    username: str
    password: str
    host_override: str
    from_override: str
    username_override: str
    port_override: str
    use_starttls_override: bool | None
    has_password: bool
    source: str  # ui | env | mixed | none

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)


def _get_raw(key: str) -> str | None:
    from app.services.settings_service import get_app_setting

    return get_app_setting(key)


def _set_raw(key: str, value: str) -> None:
    from app.services.settings_service import set_app_setting

    set_app_setting(key, value)


def _split_list(raw: str, sep: str = ",") -> list[str]:
    return [item.strip() for item in (raw or "").split(sep) if item.strip()]


def _parse_optional_date(raw: str | None) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _pref(override: str | None, fallback: str) -> str:
    text = (override or "").strip()
    return text if text else (fallback or "").strip()


def _decrypt_setting(key: str) -> str:
    token = (_get_raw(key) or "").strip()
    if not token:
        return ""
    try:
        return decrypt(token)
    except (CryptoError, CryptoNotConfigured):
        return ""


def _store_secret(key: str, plaintext: str) -> None:
    try:
        _set_raw(key, encrypt(plaintext))
    except CryptoNotConfigured as exc:
        raise ValueError(str(exc)) from exc


def get_ldap_bind_settings() -> LdapBindSettings:
    cfg = current_app.config
    host = str(cfg.get("LDAP_HOST") or "").strip()
    base_dn = str(cfg.get("LDAP_BASE_DN") or "").strip()
    domain = str(cfg.get("LDAP_DOMAIN") or "").strip()
    env_dn = str(cfg.get("LDAP_BIND_DN") or "").strip()
    env_password = str(cfg.get("LDAP_BIND_PASSWORD") or "")

    override_dn = (_get_raw(PWD_LDAP_BIND_DN) or "").strip()
    ui_password = _decrypt_setting(PWD_LDAP_BIND_PASSWORD)

    bind_dn = _pref(override_dn, env_dn)
    bind_password = ui_password if ui_password else env_password

    ui_touched = bool(override_dn or ui_password)
    env_touched = bool(env_dn or env_password)
    dn_from_ui = bool(override_dn)
    pwd_from_ui = bool(ui_password)
    if not bind_dn and not bind_password:
        source = "none"
    elif dn_from_ui and pwd_from_ui:
        source = "ui"
    elif not dn_from_ui and not pwd_from_ui and env_touched:
        source = "env"
    elif ui_touched and env_touched:
        source = "mixed"
    elif ui_touched:
        source = "ui"
    else:
        source = "env"

    return LdapBindSettings(
        host=host,
        base_dn=base_dn,
        domain=domain,
        bind_dn=bind_dn,
        bind_password=bind_password,
        bind_dn_override=override_dn,
        has_bind_password=bool(bind_password),
        source=source,
    )


def get_smtp_settings() -> SmtpSettings:
    cfg = current_app.config
    env_host = str(cfg.get("SMTP_HOST") or "").strip()
    env_from = str(cfg.get("SMTP_FROM") or "").strip()
    env_user = str(cfg.get("SMTP_USER") or "").strip()
    env_password = str(cfg.get("SMTP_PASSWORD") or "")
    try:
        env_port = int(cfg.get("SMTP_PORT") or 25)
    except (TypeError, ValueError):
        env_port = 25
    env_starttls = bool(cfg.get("SMTP_USE_STARTTLS", False))

    host_override = (_get_raw(PWD_SMTP_HOST) or "").strip()
    from_override = (_get_raw(PWD_SMTP_FROM) or "").strip()
    user_override = (_get_raw(PWD_SMTP_USER) or "").strip()
    port_override = (_get_raw(PWD_SMTP_PORT) or "").strip()
    starttls_raw = _get_raw(PWD_SMTP_USE_STARTTLS)
    ui_password = _decrypt_setting(PWD_SMTP_PASSWORD)

    use_starttls_override: bool | None
    if starttls_raw is None or not str(starttls_raw).strip():
        use_starttls_override = None
        use_starttls = env_starttls
    else:
        use_starttls_override = as_truthy(starttls_raw, default=False)
        use_starttls = use_starttls_override

    host = _pref(host_override, env_host)
    from_address = _pref(from_override, env_from)
    username = _pref(user_override, env_user) or from_address
    password = ui_password if ui_password else env_password

    if port_override:
        try:
            port = int(port_override)
        except ValueError:
            port = env_port
    else:
        port = env_port

    ui_fields = sum(
        [
            bool(host_override),
            bool(from_override),
            bool(user_override),
            bool(port_override),
            use_starttls_override is not None,
            bool(ui_password),
        ]
    )
    env_fields = sum(
        [
            bool(env_host),
            bool(env_from),
            bool(env_user),
            bool(env_password),
        ]
    )
    if not host and not from_address:
        source = "none"
    elif ui_fields and not any(
        [
            bool(env_host) and not host_override,
            bool(env_from) and not from_override,
            bool(env_password) and not ui_password,
            bool(env_user) and not user_override and not from_override,
        ]
    ):
        source = "ui"
    elif env_fields and ui_fields == 0:
        source = "env"
    elif ui_fields and env_fields:
        # Полностью задано в UI (host+from+password) — считаем ui
        if host_override and from_override and ui_password:
            source = "ui"
        else:
            source = "mixed"
    else:
        source = "env"

    return SmtpSettings(
        host=host,
        port=port,
        use_starttls=use_starttls,
        from_address=from_address,
        username=username,
        password=password,
        host_override=host_override,
        from_override=from_override,
        username_override=user_override,
        port_override=port_override,
        use_starttls_override=use_starttls_override,
        has_password=bool(password),
        source=source,
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
    schedule_enabled = as_truthy(enabled_raw, default=False)

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


def set_ldap_bind_settings(
    *,
    bind_dn: str | None = None,
    bind_password: str | None = None,
) -> LdapBindSettings:
    """Сохранить bind-учётку модуля. Пустой пароль сохраняет прежний шифротекст."""
    if bind_dn is not None:
        _set_raw(PWD_LDAP_BIND_DN, bind_dn.strip())
    if bind_password is not None and bind_password != "":
        _store_secret(PWD_LDAP_BIND_PASSWORD, bind_password)
    db.session.commit()
    return get_ldap_bind_settings()


def set_smtp_settings(
    *,
    host: str | None = None,
    port: int | str | None = None,
    use_starttls: bool | None = None,
    from_address: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> SmtpSettings:
    """Сохранить SMTP модуля. Пустой пароль сохраняет прежний шифротекст."""
    if host is not None:
        _set_raw(PWD_SMTP_HOST, host.strip())
    if from_address is not None:
        _set_raw(PWD_SMTP_FROM, from_address.strip())
    if username is not None:
        _set_raw(PWD_SMTP_USER, username.strip())
    if port is not None:
        text = str(port).strip()
        if text:
            try:
                value = int(text)
            except ValueError as exc:
                raise ValueError("SMTP-порт должен быть числом") from exc
            if value < 1 or value > 65535:
                raise ValueError("SMTP-порт должен быть в диапазоне 1–65535")
            _set_raw(PWD_SMTP_PORT, str(value))
        else:
            _set_raw(PWD_SMTP_PORT, "")
    if use_starttls is not None:
        _set_raw(PWD_SMTP_USE_STARTTLS, "1" if use_starttls else "0")
    if password is not None and password != "":
        _store_secret(PWD_SMTP_PASSWORD, password)
    db.session.commit()
    return get_smtp_settings()


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
