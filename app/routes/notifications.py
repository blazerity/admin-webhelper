"""API in-app уведомлений (W3-05 backend)."""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.services import notification_service
from app.utils import as_truthy, parse_optional_int

bp = Blueprint("notifications", __name__)


def _parse_ids_payload() -> list[int] | None:
    """ids из JSON или form. None — пометить все; [] — пустой список (noop)."""
    if request.is_json:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return None
        if "ids" not in payload and "all" not in payload:
            return None
        if as_truthy(payload.get("all"), default=False):
            return None
        raw = payload.get("ids")
        if raw is None:
            return None
        if isinstance(raw, int):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        values: list[int] = []
        for item in raw:
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                continue
        return values

    if as_truthy(request.form.get("all"), default=False):
        return None
    raw_list = request.form.getlist("ids") or request.form.getlist("ids[]")
    if not raw_list:
        single = request.form.get("ids")
        if single is None:
            return None
        raw_list = [single]
    values = []
    for item in raw_list:
        for part in str(item).split(","):
            number = parse_optional_int(part)
            if number is not None:
                values.append(number)
    return values


@bp.get("/api/notifications")
@login_required
def list_notifications():
    limit = parse_optional_int(request.args.get("limit")) or 50
    payload = notification_service.list_for_user(current_user.id, limit=limit)
    return jsonify(payload)


@bp.post("/api/notifications/read")
@login_required
def mark_notifications_read():
    ids = _parse_ids_payload()
    updated = notification_service.mark_read(current_user.id, ids)
    return jsonify({"ok": True, "updated": updated})
