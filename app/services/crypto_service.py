"""Шифрование пароля учётной записи удалённого администратора.

Используется Fernet (симметричное шифрование из пакета cryptography).
Ключ НЕ лежит в базе и не зашит в код: переменная окружения FERNET_KEY.

Сгенерировать ключ:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Потеря ключа означает, что сохранённый пароль расшифровать нельзя.
Тогда пароль вводят заново, а новый ключ кладут в .env и в EnvironmentFile.
"""

import os

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class CryptoNotConfigured(RuntimeError):
    """FERNET_KEY пустой — сохранять и читать пароль нельзя."""


class CryptoError(RuntimeError):
    """Ключ не подошёл или строка в БД повреждена."""


def _key_bytes() -> bytes:
    key = ""
    try:
        key = (current_app.config.get("FERNET_KEY") or "").strip()
    except RuntimeError:
        key = ""
    if not key:
        key = os.environ.get("FERNET_KEY", "").strip()
    if not key:
        raise CryptoNotConfigured(
            "Не задан FERNET_KEY. Сгенерируйте ключ Fernet и положите его в .env."
        )
    return key.encode("ascii")


def get_fernet() -> Fernet:
    try:
        return Fernet(_key_bytes())
    except (ValueError, CryptoNotConfigured) as exc:
        if isinstance(exc, CryptoNotConfigured):
            raise
        raise CryptoNotConfigured("FERNET_KEY не похож на ключ Fernet.") from exc


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        plaintext = ""
    token = get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("Не удалось расшифровать пароль. Проверьте FERNET_KEY.") from exc
