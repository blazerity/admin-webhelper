"""Общие куски моделей.

TimestampMixin добавляет created_at / updated_at.
onupdate срабатывает на стороне Python (ORM), когда SQLAlchemy
делает UPDATE изменённого объекта. Отдельный триггер в PostgreSQL
пока не нужен.
"""

from app.extensions import db
from app.utils import utcnow


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
