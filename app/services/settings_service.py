"""Чтение и запись простых настроек из app_settings.

Интервал опроса можно поменять без правки .env и без перезапуска
кода: планировщик при каждом цикле спрашивает get_poll_interval_seconds().
APScheduler в scheduler_worker пересоздаёт интервал, прочитав это значение.

Учётка WMI (discovery) — глобальная, одна на всё приложение.
Пароль только как Fernet-шифротекст; ключ — FERNET_KEY в окружении.

Sudo-учётка для управления службами (Параметры → Управление службами) —
Linux-логин и опционально пароль (Fernet). Используется для перезапуска
после обновления и для start/enable/stop служб отчётов
(`bawh-password-reports`, `bawh-pc-reports`) при тумблерах.
С паролем — через su, без отдельного sudoers. Без пароля — sudo -n
(нужен bawh-update.sudoers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import POLL_INTERVAL_KEY, AppSetting
from app.services.crypto_service import decrypt, encrypt

MIN_POLL_SECONDS = 30
MAX_POLL_SECONDS = 24 * 60 * 60

DISCOVERY_USERNAME_KEY = "discovery_username"
DISCOVERY_DOMAIN_KEY = "discovery_domain"
DISCOVERY_PASSWORD_KEY = "discovery_password_encrypted"
UPDATE_SUDO_USER_KEY = "update_sudo_user"
UPDATE_SUDO_PASSWORD_KEY = "update_sudo_password_encrypted"
# Ветка self-update. Если задана, важнее GIT_BRANCH из .env.
UPDATE_GIT_BRANCH_KEY = "update_git_branch"

# Как useradd: начинается с буквы/_, дальше буквы, цифры, _, -.
_LINUX_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class DiscoveryCredentialsError(RuntimeError):
    """Нельзя сохранить учётку WMI в таком виде."""


class UpdateSudoUserError(RuntimeError):
    """Некорректные данные Linux-учётки для перезапуска служб."""


@dataclass(frozen=True)
class DiscoveryCredentialView:
    """Поля для формы: пароль на страницу не отдаём."""

    username: str
    domain: str
    password_set: bool


@dataclass(frozen=True)
class StoredDiscoveryCredentials:
    """Расшифрованная учётка из БД для WMI."""

    username: str
    domain: str
    password: str

    def __repr__(self) -> str:
        return (
            f"StoredDiscoveryCredentials(username={self.username!r}, "
            f"domain={self.domain!r}, password='***')"
        )


@dataclass(frozen=True)
class UpdateSudoView:
    """Поля формы sudo-учётки: пароль на страницу не отдаём."""

    username: str
    password_set: bool


@dataclass(frozen=True)
class UpdateSudoCredentials:
    """Расшифрованная sudo-учётка для перезапуска служб."""

    username: str
    password: str

    def __repr__(self) -> str:
        return (
            f"UpdateSudoCredentials(username={self.username!r}, password='***')"
        )


def get_poll_interval_seconds() -> int:
    row = db.session.get(AppSetting, POLL_INTERVAL_KEY)
    if row is not None:
        try:
            value = int(row.value)
        except (TypeError, ValueError):
            value = int(current_app.config.get("POLL_INTERVAL_SECONDS", 300))
    else:
        value = int(current_app.config.get("POLL_INTERVAL_SECONDS", 300))
    return max(MIN_POLL_SECONDS, min(MAX_POLL_SECONDS, value))


def set_poll_interval_seconds(value: int) -> int:
    value = int(value)
    if value < MIN_POLL_SECONDS or value > MAX_POLL_SECONDS:
        raise ValueError(
            f"Интервал должен быть от {MIN_POLL_SECONDS} до {MAX_POLL_SECONDS} секунд."
        )
    row = db.session.get(AppSetting, POLL_INTERVAL_KEY)
    if row is None:
        row = AppSetting(key=POLL_INTERVAL_KEY, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)
    db.session.commit()
    return value


def get_discovery_credential_view() -> DiscoveryCredentialView:
    """Имя/домен и флаг «пароль уже задан» — без расшифровки."""
    username = (_get_setting(DISCOVERY_USERNAME_KEY) or "").strip()
    domain = (_get_setting(DISCOVERY_DOMAIN_KEY) or "").strip()
    password_set = bool((_get_setting(DISCOVERY_PASSWORD_KEY) or "").strip())
    return DiscoveryCredentialView(
        username=username,
        domain=domain,
        password_set=password_set,
    )


def get_stored_discovery_credentials() -> StoredDiscoveryCredentials | None:
    """Учётка из БД, если заполнены пользователь и шифротекст пароля."""
    username = (_get_setting(DISCOVERY_USERNAME_KEY) or "").strip()
    token = (_get_setting(DISCOVERY_PASSWORD_KEY) or "").strip()
    if not username or not token:
        return None
    domain = (_get_setting(DISCOVERY_DOMAIN_KEY) or "").strip()
    return StoredDiscoveryCredentials(
        username=username,
        domain=domain,
        password=decrypt(token),
    )


def save_discovery_credentials(
    username: str,
    domain: str,
    password: str | None,
) -> DiscoveryCredentialView:
    """Сохраняет глобальную учётку WMI. Пустое имя очищает все три ключа.

    Пустой пароль при уже заданном имени оставляет прежний шифротекст.
    """
    username = (username or "").strip()
    domain = (domain or "").strip()
    password = password or ""

    if not username:
        if password:
            raise DiscoveryCredentialsError(
                "Укажите пользователя WMI или очистите и пользователя, и пароль."
            )
        _set_setting(DISCOVERY_USERNAME_KEY, "")
        _set_setting(DISCOVERY_DOMAIN_KEY, "")
        _set_setting(DISCOVERY_PASSWORD_KEY, "")
        db.session.commit()
        return get_discovery_credential_view()

    existing = (_get_setting(DISCOVERY_PASSWORD_KEY) or "").strip()
    if password:
        token = encrypt(password)
    elif existing:
        token = existing
    else:
        raise DiscoveryCredentialsError("Пароль учётки WMI ещё не задан.")

    _set_setting(DISCOVERY_USERNAME_KEY, username)
    _set_setting(DISCOVERY_DOMAIN_KEY, domain)
    _set_setting(DISCOVERY_PASSWORD_KEY, token)
    db.session.commit()
    return get_discovery_credential_view()


def get_update_sudo_user() -> str:
    """Linux-пользователь для перезапуска служб после обновления.

    Сначала app_settings, иначе UPDATE_SUDO_USER из окружения.
    """
    stored = (_get_setting(UPDATE_SUDO_USER_KEY) or "").strip()
    if stored:
        return stored
    return str(current_app.config.get("UPDATE_SUDO_USER") or "").strip()


def get_update_sudo_view() -> UpdateSudoView:
    """Имя из БД и флаг «пароль уже задан» — без env-fallback и без расшифровки."""
    stored = (_get_setting(UPDATE_SUDO_USER_KEY) or "").strip()
    return UpdateSudoView(
        username=stored,
        password_set=bool((_get_setting(UPDATE_SUDO_PASSWORD_KEY) or "").strip()),
    )


def get_update_sudo_credentials() -> UpdateSudoCredentials:
    """Учётка для перезапуска: пользователь и пароль (пароль может быть пустым)."""
    username = get_update_sudo_user()
    token = (_get_setting(UPDATE_SUDO_PASSWORD_KEY) or "").strip()
    password = decrypt(token) if token else ""
    return UpdateSudoCredentials(username=username, password=password)


def save_update_sudo_credentials(username: str, password: str | None) -> UpdateSudoView:
    """Сохраняет sudo-учётку. Пустой логин очищает и логин, и пароль.

    Пустой пароль при уже заданном логине оставляет прежний шифротекст.
    Пароль без логина допускается (при перезапуске подставится root).
    """
    username = (username or "").strip()
    password = password or ""

    if username and not _LINUX_USER.fullmatch(username):
        raise UpdateSudoUserError(
            "Sudo-пользователь: только латиница в нижнем регистре, цифры, "
            "_ и - (как имя Linux-учётки), до 32 символов."
        )

    if not username:
        if password:
            raise UpdateSudoUserError(
                "Укажите sudo-пользователя или очистите и логин, и пароль."
            )
        _set_setting(UPDATE_SUDO_USER_KEY, "")
        _set_setting(UPDATE_SUDO_PASSWORD_KEY, "")
        db.session.commit()
        return get_update_sudo_view()

    existing = (_get_setting(UPDATE_SUDO_PASSWORD_KEY) or "").strip()
    if password:
        token = encrypt(password)
    elif existing:
        token = existing
    else:
        token = ""

    _set_setting(UPDATE_SUDO_USER_KEY, username)
    _set_setting(UPDATE_SUDO_PASSWORD_KEY, token)
    db.session.commit()
    return get_update_sudo_view()


def set_update_sudo_user(value: str) -> str:
    """Сохраняет только имя; пароль не трогает. Для совместимости."""
    view = save_update_sudo_credentials(value, "")
    return view.username


def get_app_setting(key: str) -> str | None:
    """Сырое значение из app_settings или None."""
    row = db.session.get(AppSetting, key)
    if row is None:
        return None
    return row.value


def set_app_setting(key: str, value: str) -> None:
    """Upsert строки app_settings без commit (caller коммитит)."""
    row = db.session.get(AppSetting, key)
    if row is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


# Совместимость внутри модуля.
_get_setting = get_app_setting
_set_setting = set_app_setting
