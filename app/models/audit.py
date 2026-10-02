"""Журнал критичных действий администратора (Authz v2 / W3-03)."""

from app.extensions import db
from app.utils import utcnow


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    actor_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_username = db.Column(db.String(128), nullable=False, default="")
    action = db.Column(db.String(64), nullable=False, default="")
    entity_type = db.Column(db.String(64), nullable=False, default="")
    entity_id = db.Column(db.String(64), nullable=False, default="")
    detail = db.Column(db.Text, nullable=False, default="")

    actor = db.relationship("User")

    def __repr__(self) -> str:
        return f"<AdminAuditLog {self.action} {self.entity_type}:{self.entity_id}>"
