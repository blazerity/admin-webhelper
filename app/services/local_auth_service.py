"""Локальный вход без LDAP.

Нужен для стендов и облачных агентов, у которых нет доступа к AD.
Пароль хранится только как хеш (werkzeug); открытый текст — в .env
при создании тестового администратора.
"""

from __future__ import annotations

import logging
import re

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User
from app.utils import utcnow

logger = logging.getLogger(__name__)

_USERNAME_MAX = 128
_DISPLAY_NAME_MAX = 255
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def local_auth_enabled() -> bool:
    return bool(current_app.config.get("LOCAL_AUTH_ENABLED"))


def authenticate_local(username: str, password: str) -> User | None:
    """Проверить локальный пароль. None — неверный вход или локальный режим выключен."""
    if not local_auth_enabled():
        return None
    if not isinstance(password, str) or password == "":
        return None

    typed = username.strip() if isinstance(username, str) else ""
    if not _username_allowed(typed):
        return None

    user = db.session.scalar(select(User).where(User.username == typed.lower()))
    if user is None or not user.password_hash:
        return None
    if not check_password_hash(user.password_hash, password):
        return None

    user.last_login_at = utcnow()
    db.session.commit()
    return user


def ensure_local_admin() -> User | None:
    """Создать или обновить тестового локального администратора из конфига.

    Вызывается при старте и на странице входа. Без LOCAL_AUTH_ENABLED
    и без пароля в окружении ничего не делает.
    """
    if not local_auth_enabled():
        return None

    username = str(current_app.config.get("LOCAL_ADMIN_USERNAME") or "").strip().lower()
    password = str(current_app.config.get("LOCAL_ADMIN_PASSWORD") or "")
    display_name = str(
        current_app.config.get("LOCAL_ADMIN_DISPLAY_NAME") or "Локальный администратор"
    ).strip()

    if not _username_allowed(username) or not password:
        logger.warning(
            "LOCAL_AUTH_ENABLED включён, но LOCAL_ADMIN_USERNAME/PASSWORD не заданы — "
            "тестовый администратор не создан."
        )
        return None

    try:
        user = db.session.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, display_name=display_name[:_DISPLAY_NAME_MAX])
            db.session.add(user)

        user.display_name = (display_name or username)[:_DISPLAY_NAME_MAX]
        user.is_admin = True
        user.password_hash = generate_password_hash(password)
        # Локальный админ не привязан к DN каталога.
        if not user.ldap_dn:
            user.ldap_dn = None
        db.session.commit()
        return user
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.warning("Не удалось создать локального администратора: %s", exc)
        return None


def _username_allowed(username: str) -> bool:
    if not username or len(username) > _USERNAME_MAX:
        return False
    return bool(_USERNAME_RE.fullmatch(username))
