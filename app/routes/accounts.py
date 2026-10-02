"""Справочник УЗ на конечных точках и карточка пользователя."""

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.services.account_service import (
    account_current_devices,
    account_device_sightings,
    get_visible_account_or_404,
    list_visible_accounts,
)

bp = Blueprint("accounts", __name__, url_prefix="/accounts")


@bp.get("/")
@login_required
def list_accounts():
    """Список УЗ, замеченных на доступных устройствах."""
    query = (request.args.get("q") or "").strip()
    accounts = list_visible_accounts(current_user, query=query)
    return render_template(
        "accounts/list.html",
        accounts=accounts,
        search_query=query,
    )


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
