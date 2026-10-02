"""Поиск устройств.

Эндпоинты:
- search_page  GET /search
- suggest      GET /search/suggest   (JSON для автодополнения)
"""

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.authz import accessible_sectors
from app.services.search_service import search_devices, suggest_devices

bp = Blueprint("search", __name__)


def _sector_id_from_args() -> int | None:
    """?sector_id= как число. Пустое или нечисло — значит «все доступные»."""
    raw = (request.args.get("sector_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@bp.get("/search")
@login_required
def search_page():
    """Страница поиска: строка, список секторов и таблица результатов."""
    query = (request.args.get("q") or "").strip()
    sector_id = _sector_id_from_args()
    return render_template(
        "search/search.html",
        results=search_devices(current_user, query, sector_id=sector_id),
        query=query,
        sectors=accessible_sectors(current_user),
        sector_id=sector_id,
    )


@bp.get("/search/suggest")
@login_required
def suggest():
    """JSON-подсказки: список {label, url} для /search/suggest?q=."""
    query = request.args.get("q") or ""
    return jsonify(suggest_devices(current_user, query))
