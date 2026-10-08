"""Справочник УЗ на конечных точках и карточка пользователя."""

from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app.services.account_service import (
    account_current_devices,
    account_device_sightings,
    count_account_device_sightings,
    get_visible_account_or_404,
    list_visible_accounts,
)
from app.services.export_service import csv_attachment, export_accounts_csv
from app.utils import as_truthy, format_utc, normalize_page, parse_optional_int

bp = Blueprint("accounts", __name__, url_prefix="/accounts")

_PREVIEW_LIMIT = 5
_HISTORY_PER_PAGE = 20


def _wants_json() -> bool:
    if (request.args.get("format") or "").strip().lower() == "json":
        return True
    return (request.accept_mimetypes.best or "") == "application/json"


def _accounts_payload(result: dict) -> dict:
    items = []
    for account in result["items"]:
        items.append(
            {
                "id": account.id,
                "account_key": account.account_key,
                "display_name": account.display_name or "",
                "first_seen_at": format_utc(account.first_seen_at),
                "last_seen_at": format_utc(account.last_seen_at),
                "url": url_for("accounts.detail", account_id=account.id),
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
def list_accounts():
    """Список УЗ, замеченных на доступных устройствах."""
    page, per_page = normalize_page(
        parse_optional_int(request.args.get("page")),
        parse_optional_int(request.args.get("per_page")),
    )
    query = (request.args.get("q") or "").strip()
    result = list_visible_accounts(
        current_user,
        q=query,
        page=page,
        per_page=per_page,
    )
    if _wants_json():
        return jsonify(_accounts_payload(result))
    show_display_name = any((a.display_name or "").strip() for a in result["items"])
    return render_template(
        "accounts/list.html",
        accounts=result["items"],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        search_query=query,
        show_display_name=show_display_name,
    )


@bp.get("/export.csv")
@login_required
def export_csv():
    """CSV видимых УЗ (те же ACL, что список)."""
    query = (request.args.get("q") or "").strip()
    return csv_attachment(export_accounts_csv(current_user, q=query), "accounts.csv")


@bp.get("/<int:account_id>")
@login_required
def detail(account_id: int):
    """Карточка УЗ: где сейчас и где видели раньше."""
    account = get_visible_account_or_404(current_user, account_id)
    show_all = as_truthy(request.args.get("all"))
    list_total = count_account_device_sightings(account, current_user)
    if show_all:
        page, per_page = normalize_page(
            parse_optional_int(request.args.get("page")),
            parse_optional_int(request.args.get("per_page")) or _HISTORY_PER_PAGE,
            max_per_page=100,
        )
        offset = (page - 1) * per_page
        sightings = account_device_sightings(
            account, current_user, limit=per_page, offset=offset
        )
    else:
        page, per_page = 1, _PREVIEW_LIMIT
        sightings = account_device_sightings(
            account, current_user, limit=_PREVIEW_LIMIT
        )
    show_raw_value = any((row.raw_value or "").strip() for row in sightings)
    return render_template(
        "accounts/detail.html",
        account=account,
        current_devices=account_current_devices(account, current_user),
        sightings=sightings,
        show_raw_value=show_raw_value,
        show_all=show_all,
        list_page=page,
        list_per_page=per_page,
        list_total=list_total,
        has_more=(not show_all and list_total > len(sightings)),
    )
