"""Откуда брать логин и пароль для PsExec.

Своя учётка пользователя → иначе вход на сайт (LDAP_DOMAIN + пароль входа).
Открытый пароль в лог, шаблон и flash не попадает.
"""

from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import RemoteCredential, User
from app.services.crypto_service import decrypt, encrypt


class CredentialsNotConfigured(RuntimeError):
    """Для этого пользователя нельзя собрать учётку PsExec."""


@dataclass
class RemoteAdminCredentials:
    username: str
    domain: str
    password: str

    def __repr__(self) -> str:
        # Пароль не должен попасть в лог при печати объекта.
        return (
            f"RemoteAdminCredentials(username={self.username!r}, "
            f"domain={self.domain!r}, password='***')"
        )


def get_stored_credential(user_id: int) -> RemoteCredential | None:
    return RemoteCredential.query.filter_by(user_id=user_id).one_or_none()


def get_remote_admin_credentials(user_id: int) -> RemoteAdminCredentials:
    """Учётка PsExec того, кто запустил команду (чужую строку не читает)."""
    row = get_stored_credential(user_id)
    if row and row.username and row.password_encrypted:
        return RemoteAdminCredentials(
            username=row.username,
            domain=row.domain or "",
            password=decrypt(row.password_encrypted),
        )
    login = _credentials_from_site_login(user_id, row)
    if login is not None:
        return login
    raise CredentialsNotConfigured(
        "Учётка PsExec не задана. Оставьте поля пустыми и войдите на сайт заново, "
        "чтобы команда пошла от вашего входа, или заполните пользователя и пароль PsExec."
    )


def save_remote_admin_credentials(
    username: str,
    domain: str,
    password: str | None,
    user_id: int,
) -> RemoteCredential:
    """Сохраняет учётку; пустые имя/пароль очищают свою и оставляют вход на сайт.

    Пустой пароль при уже заданном имени сохраняет прежний шифротекст.
    """
    username = (username or "").strip()
    domain = (domain or "").strip()
    row = get_stored_credential(user_id)
    if row is None:
        row = RemoteCredential(user_id=user_id)
        db.session.add(row)
    row.updated_by_id = user_id
    if not username:
        if password:
            raise CredentialsNotConfigured(
                "Укажите пользователя PsExec или оставьте и пользователя, и пароль пустыми."
            )
        row.username = ""
        row.domain = ""
        row.password_encrypted = ""
        db.session.commit()
        return row
    row.username = username
    row.domain = domain
    if password:
        row.password_encrypted = encrypt(password)
    elif not row.password_encrypted:
        raise CredentialsNotConfigured("Пароль PsExec ещё не задан.")
    db.session.commit()
    return row


def remember_login_password(user_id: int, password: str) -> None:
    """Шифрует пароль входа; отдельно заданные поля PsExec не трогает."""
    secret = password or ""
    if not secret:
        return
    row = get_stored_credential(user_id)
    if row is None:
        row = RemoteCredential(user_id=user_id)
        db.session.add(row)
    row.login_password_encrypted = encrypt(secret)
    db.session.commit()


def _credentials_from_site_login(
    user_id: int,
    row: RemoteCredential | None,
) -> RemoteAdminCredentials | None:
    if row is None or not row.login_password_encrypted:
        return None
    user = db.session.get(User, user_id)
    if user is None or not (user.username or "").strip():
        return None
    domain = (current_app.config.get("LDAP_DOMAIN") or "").strip()
    return RemoteAdminCredentials(
        username=user.username.strip(),
        domain=domain,
        password=decrypt(row.login_password_encrypted),
    )
