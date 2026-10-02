"""Вспомогательные функции без зависимости от Flask-запроса."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """UTC с tzinfo — чтобы метки в БД не путались с локальным временем сервера."""
    return datetime.now(timezone.utc)


def clip(text: str | None, limit: int) -> str:
    """Обрезает длинный текст лога, чтобы не раздувать строку в PostgreSQL."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[лог обрезан]"
