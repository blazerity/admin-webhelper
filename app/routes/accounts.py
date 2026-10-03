"""Справочник УЗ на конечных точках и карточка пользователя."""

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.services.account_service import (
    account_current_devices,
    account_device_sightings,
    get_visible_account_or_404,
    list_visible_accounts,
)
from app.services.export_service import csv_attachment, export_accounts_csv
from app.utils import normalize_page, parse_optional_int

bp = Blueprint("accounts", __name__, url_prefix="/accounts")


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
    return render_template(
        "accounts/list.html",
        accounts=result["items"],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        search_query=query,
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
    return render_template(
        "accounts/detail.html",
        account=account,
        current_devices=account_current_devices(account, current_user),
        sightings=account_device_sightings(account, current_user, limit=50),
    )
