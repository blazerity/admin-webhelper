"""Лента действий в системе (справочник типов + события из журналов)."""

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.services.action_service import list_action_kinds, list_system_actions
from app.services.export_service import csv_attachment, export_actions_csv
from app.utils import normalize_page, parse_optional_int

bp = Blueprint("actions", __name__, url_prefix="/actions")


@bp.get("/")
@login_required
def list_actions():
    """Недавние действия на доступных устройствах."""
    page, per_page = normalize_page(
        parse_optional_int(request.args.get("page")),
        parse_optional_int(request.args.get("per_page")),
    )
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
    return csv_attachment(export_actions_csv(current_user, kind=kind), "actions.csv")
