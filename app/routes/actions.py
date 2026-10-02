"""Лента действий в системе (справочник типов + события из журналов)."""

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.services.action_service import list_action_kinds, list_system_actions
from app.utils import parse_optional_int

bp = Blueprint("actions", __name__, url_prefix="/actions")


def _page_args() -> tuple[int, int]:
    page = parse_optional_int(request.args.get("page")) or 1
    per_page = parse_optional_int(request.args.get("per_page")) or 50
    return max(1, page), max(1, min(per_page, 200))


@bp.get("/")
@login_required
def list_actions():
    """Недавние действия на доступных устройствах."""
    page, per_page = _page_args()
    q = (request.args.get("q") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    kinds = list_action_kinds()
    result = list_system_actions(
        current_user,
        page=page,
        per_page=per_page,
        q=q,
        kind=kind,
    )
    return render_template(
        "actions/list.html",
        action_kinds=kinds,
        actions=result["items"],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        search_query=q,
        kind_filter=kind,
    )
