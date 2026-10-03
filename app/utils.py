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


def format_utc(value: datetime | None) -> str:
    """Человекочитаемая метка UTC для UI; пустое значение — тире."""
    if not value:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_optional_int(raw: str | None) -> int | None:
    """Число из строки формы/query; пустое или нечисло — None."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def normalize_page(
    page: int | None = 1,
    per_page: int | None = 50,
    *,
    max_per_page: int = 200,
) -> tuple[int, int]:
    """Общий clamp page/per_page для списков и экспорта."""
    page_n = max(1, int(page or 1))
    per_page_n = max(1, min(int(per_page or 50), max_per_page))
    return page_n, per_page_n


def as_truthy(value: object, default: bool = False) -> bool:
    """Строка «false» не должна стать True через обычный bool()."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
