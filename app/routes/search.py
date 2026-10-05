"""Поиск устройств: старый URL редиректит на карту."""

from flask import Blueprint, redirect, request, url_for
from flask_login import login_required

from app.utils import parse_optional_int

bp = Blueprint("search", __name__)


@bp.get("/search")
@login_required
def search_page():
    """Старый адрес поиска ведёт на карту с полем поиска."""
    args = {}
    query = (request.args.get("q") or "").strip()
    sector_id = parse_optional_int(request.args.get("sector_id"))
    if query:
        args["q"] = query
    if sector_id is not None:
        args["sector_id"] = sector_id
    return redirect(url_for("devices.map", **args))
