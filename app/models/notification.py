"""In-app уведомления (колокольчик / API)."""

from app.extensions import db
from app.utils import utcnow


class NotificationKind:
    SCRIPT_FAILED = "script_failed"


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.String(32), nullable=False, default="")
    title = db.Column(db.String(255), nullable=False, default="")
    body = db.Column(db.Text, nullable=False, default="")
    link_url = db.Column(db.String(512), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User")

    __table_args__ = (
        db.Index("ix_notifications_user_read", "user_id", "read_at"),
    )

    def __repr__(self) -> str:
        return f"<Notification {self.kind} user={self.user_id}>"
