"""TimestampMixin: created_at / updated_at (onupdate на стороне ORM)."""

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
