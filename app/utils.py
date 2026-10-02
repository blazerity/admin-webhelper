"""Мелкие функции без бизнес-логики.

Сюда попадает только то, что не зависит от Flask-запроса:
время, обрезка длинных логов. Правила доступа — в app/authz.py,
работа с адресами — в app/services/net_utils.py.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Текущее время в UTC с таймзоной.

    Naive datetime (без tzinfo) легко перепутать с локальным временем
    сервера. Все метки в БД пишем через эту функцию.
    """
    return datetime.now(timezone.utc)


def clip(text: str | None, limit: int) -> str:
    """Обрезает длинный текст, чтобы лог выполнения не раздувал строку в PostgreSQL."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[лог обрезан]"
