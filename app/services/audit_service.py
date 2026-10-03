"""Запись критичных админ-действий в admin_audit_log."""

from __future__ import annotations

from sqlalchemy import select

from app.extensions import db
from app.models.audit import AdminAuditLog
from app.utils import utcnow


def log(actor, action: str, entity_type: str, entity_id, detail: str = "") -> AdminAuditLog:
    """Добавить строку аудита и зафиксировать транзакцию.

    ``actor`` — User или None (системное действие). ``entity_id`` приводится к str.
    """
    username = ""
    user_id = None
    if actor is not None:
        user_id = getattr(actor, "id", None)
        username = (getattr(actor, "username", None) or getattr(actor, "display_name", None) or "")
        username = str(username).strip()

    row = AdminAuditLog(
        created_at=utcnow(),
        actor_user_id=user_id,
        actor_username=username[:128],
        action=(action or "").strip()[:64],
        entity_type=(entity_type or "").strip()[:64],
        entity_id=str(entity_id if entity_id is not None else "")[:64],
        detail=detail if detail is not None else "",
    )
    db.session.add(row)
    db.session.commit()
    return row


def list_audit_entries(*, limit: int = 100) -> list[AdminAuditLog]:
    """Недавние записи для GET /admin/audit (новые сверху)."""
    limit = max(1, min(int(limit or 100), 500))
    stmt = (
        select(AdminAuditLog)
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(limit)
    )
    return list(db.session.scalars(stmt).all())
