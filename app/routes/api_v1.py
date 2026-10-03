"""Внутренний API v1: aliases на существующие JSON-эндпоинты.

Старые пути не удаляем — только добавляем `/api/v1/...` с тем же JSON.
"""

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app.authz import admin_required
from app.routes.devices import map_status_payload
from app.routes.search import _search_json_payload
from app.services.batch_service import batch_status_payload
from app.services.command_presets import list_command_presets
from app.services.network_summary_service import get_network_summary
from app.services.notification_service import list_for_user
from app.utils import utcnow

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


@bp.get("/network/summary")
@login_required
def network_summary():
    return jsonify(get_network_summary(current_user))


@bp.get("/command-presets")
@admin_required
def command_presets():
    return jsonify({"presets": list_command_presets()})


@bp.get("/map/status")
@login_required
def map_status():
    return jsonify(map_status_payload())


@bp.get("/batches/<batch_id>")
@login_required
def batch_status(batch_id: str):
    payload = batch_status_payload(current_user, batch_id)
    if payload is None:
        abort(404)
    return jsonify(payload)


@bp.get("/notifications")
@login_required
def notifications():
    return jsonify(list_for_user(current_user.id))


@bp.get("/search/suggest")
@login_required
def search_suggest():
    """Autocomplete: тот же поиск, что /search/suggest, укороченные поля."""
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"query": query, "results": [], "updated_at": utcnow().isoformat()})
    results = [
        {
            "id": item["id"],
            "hostname": item["hostname"],
            "ip": item["ip"],
            "status": item["status"],
            "url": item["url"],
        }
        for item in _search_json_payload()
    ]
    return jsonify({"query": query, "results": results, "updated_at": utcnow().isoformat()})


@bp.get("/health")
def health_alias():
    return jsonify({"status": "ok"})
