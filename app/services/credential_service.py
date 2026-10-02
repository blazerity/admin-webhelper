"""Откуда брать логин и пароль для PsExec.

Порядок:
1. Строка remote_credentials (пароль в БД зашифрован).
2. Если строки нет — переменные PSEXEC_USERNAME / PSEXEC_DOMAIN / PSEXEC_PASSWORD.

TODO: подставьте реальную учётную запись администратора Windows
через форму «Настройки» или через .env. В коде пароля нет специально.

После сохранения пароля в БД уберите PSEXEC_PASSWORD из .env,
чтобы открытый пароль не лежал в файле окружения.
"""

from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import DEFAULT_CREDENTIAL_NAME, RemoteCredential
from app.services.crypto_service import decrypt, encrypt


class CredentialsNotConfigured(RuntimeError):
    """Нет ни записи в БД, ни переменных окружения."""


@dataclass
class RemoteAdminCredentials:
    username: str
    domain: str
    password: str

    def __repr__(self) -> str:
        # Пароль не должен попасть в лог, если кто-то напечатает объект.
        return (
            f"RemoteAdminCredentials(username={self.username!r}, "
            f"domain={self.domain!r}, password='***')"
        )


def get_stored_credential() -> RemoteCredential | None:
    return RemoteCredential.query.filter_by(name=DEFAULT_CREDENTIAL_NAME).one_or_none()


def credentials_configured() -> bool:
    row = get_stored_credential()
    if row and row.username and row.password_encrypted:
        return True
    return bool(current_app.config.get("PSEXEC_USERNAME") and current_app.config.get("PSEXEC_PASSWORD"))


def get_remote_admin_credentials() -> RemoteAdminCredentials:
    row = get_stored_credential()
    if row and row.username and row.password_encrypted:
        return RemoteAdminCredentials(
            username=row.username,
            domain=row.domain or "",
            password=decrypt(row.password_encrypted),
        )
    username = (current_app.config.get("PSEXEC_USERNAME") or "").strip()
    password = current_app.config.get("PSEXEC_PASSWORD") or ""
    domain = (current_app.config.get("PSEXEC_DOMAIN") or "").strip()
    if username and password:
        return RemoteAdminCredentials(username=username, domain=domain, password=password)
    raise CredentialsNotConfigured(
        "Учётная запись PsExec не задана. Заполните форму администратора "
        "или переменные PSEXEC_USERNAME, PSEXEC_DOMAIN, PSEXEC_PASSWORD."
    )


def save_remote_admin_credentials(
    username: str,
    domain: str,
    password: str | None,
    updated_by_id: int | None,
) -> RemoteCredential:
    """Сохраняет учётку. Пустой password оставляет прежний шифротекст.

    Так форма «изменить только логин» не затирает пароль.
    """
    username = (username or "").strip()
    domain = (domain or "").strip()
    if not username:
        raise CredentialsNotConfigured("Имя пользователя PsExec пустое.")

    row = get_stored_credential()
    if row is None:
        row = RemoteCredential(name=DEFAULT_CREDENTIAL_NAME)
        db.session.add(row)
    row.username = username
    row.domain = domain
    row.updated_by_id = updated_by_id
    if password:
        row.password_encrypted = encrypt(password)
    elif not row.password_encrypted:
        raise CredentialsNotConfigured("Пароль PsExec ещё не задан.")
    db.session.commit()
    return row
