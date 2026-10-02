"""Лента действий в системе (справочник типов + события из журналов)."""

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.services.action_service import list_system_actions, list_action_kinds

bp = Blueprint("actions", __name__, url_prefix="/actions")


@bp.get("/")
@login_required
def list_actions():
    """Недавние действия на доступных устройствах."""
    kinds = list_action_kinds()
    items = list_system_actions(current_user, limit=80)
    return render_template(
        "actions/list.html",
        action_kinds=kinds,
        actions=items,
    )
