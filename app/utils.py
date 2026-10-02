"""Вспомогательные функции без зависимости от Flask-запроса."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """UTC с tzinfo — метки в БД не путаются с локальным временем сервера."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Приводит datetime к UTC. Naive (часто из SQLite) считаем уже UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def clip(text: str | None, limit: int) -> str:
    """Обрезает длинный текст лога."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[лог обрезан]"


def ilike_pattern(text: str) -> str:
    """Подстрока для ILIKE: экранирует % _ \\ и оборачивает в %...%."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
