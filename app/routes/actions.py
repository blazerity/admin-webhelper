"""Лента действий в системе (справочник типов + события из журналов)."""

from flask import Blueprint, Response, render_template, request
from flask_login import current_user, login_required

from app.services.action_service import list_action_kinds, list_system_actions
from app.services.export_service import export_actions_csv
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


@bp.get("/export.csv")
@login_required
def export_csv():
    """CSV ленты действий с теми же фильтрами видимости."""
    kind = (request.args.get("kind") or "").strip()
    body = export_actions_csv(current_user, kind=kind)
    return Response(
        body,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=actions.csv"},
    )
