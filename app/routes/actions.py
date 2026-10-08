"""Лента действий в системе (справочник типов + события из журналов)."""

from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app.services import audit_service
from app.services.action_service import list_action_kinds, list_system_actions
from app.services.export_service import csv_attachment, export_actions_csv
from app.utils import as_truthy, format_utc, normalize_page, parse_optional_int

bp = Blueprint("actions", __name__, url_prefix="/actions")

_PREVIEW_LIMIT = 5
_HISTORY_PER_PAGE = 20


def _wants_json() -> bool:
    if (request.args.get("format") or "").strip().lower() == "json":
        return True
    return (request.accept_mimetypes.best or "") == "application/json"


def _actions_payload(result: dict) -> dict:
    items = []
    for item in result["items"]:
        items.append(
            {
                "kind_code": item.kind_code,
                "kind_title": item.kind_title,
                "title": item.title,
                "status": item.status,
                "when": format_utc(item.when),
                "device_id": item.device_id,
                "device_label": item.device_label,
                "actor": item.actor,
                "url": item.url,
                "device_url": (
                    url_for("devices.detail", device_id=item.device_id)
                    if item.device_id
                    else None
                ),
            }
        )
    return {
        "items": items,
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }


@bp.get("/")
@login_required
def list_actions():
    """Недавние действия на доступных устройствах."""
    show_all = as_truthy(request.args.get("all"))
    q = (request.args.get("q") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    # Поиск или фильтр по типу — сразу полный список, иначе превью 5.
    if q or kind:
        show_all = True

    if show_all:
        page, per_page = normalize_page(
            parse_optional_int(request.args.get("page")),
            parse_optional_int(request.args.get("per_page")) or _HISTORY_PER_PAGE,
            max_per_page=200,
        )
    else:
        page, per_page = 1, _PREVIEW_LIMIT

    kinds = list_action_kinds()
    result = list_system_actions(
        current_user,
        page=page,
        per_page=per_page,
        q=q,
        kind=kind,
    )
    if _wants_json():
        return jsonify(_actions_payload(result))

    audit_entries = []
    audit_total = 0
    audit_show_all = False
    if getattr(current_user, "is_admin", False):
        audit_total = audit_service.count_audit_entries()
        audit_show_all = as_truthy(request.args.get("audit_all"))
        # Полный журнал без отдельной пагинации (как раньше до 200),
        # чтобы не пересекаться с page ленты действий на той же странице.
        audit_entries = audit_service.list_audit_entries(
            limit=200 if audit_show_all else _PREVIEW_LIMIT
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
        show_all=show_all,
        has_more=(not show_all and result["total"] > len(result["items"])),
        audit_entries=audit_entries,
        audit_total=audit_total,
        audit_show_all=audit_show_all,
    )


@bp.get("/export.csv")
@login_required
def export_csv():
    """CSV ленты действий с теми же фильтрами видимости."""
    kind = (request.args.get("kind") or "").strip()
    return csv_attachment(export_actions_csv(current_user, kind=kind), "actions.csv")
