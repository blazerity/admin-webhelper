"""Создание схемы БД из моделей (без Alembic)."""

from sqlalchemy import func, inspect, select

from app.extensions import db
from app.models import ActionKind, seed_action_kinds


def ensure_schema() -> None:
    """Создать недостающие таблицы и заполнить справочник action_kinds."""
    existing = set(inspect(db.engine).get_table_names())
    if not set(db.metadata.tables).issubset(existing):
        db.create_all()

    count = db.session.scalar(select(func.count()).select_from(ActionKind)) or 0
    if count == 0:
        seed_action_kinds()
        db.session.commit()
