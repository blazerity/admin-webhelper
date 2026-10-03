"""Внутренний API v1: тонкие aliases на существующие JSON-view.

Старые пути не удаляем — `/api/v1/...` делегирует в legacy handlers.
"""

from flask import Blueprint, jsonify

from app.authz import admin_required
from flask_login import login_required

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


@bp.get("/network/summary")
@login_required
def network_summary():
    from app.routes.devices import network_summary as legacy

    return legacy()


@bp.get("/command-presets")
@admin_required
def command_presets():
    from app.routes.devices import command_presets_api as legacy

    return legacy()


@bp.get("/map/status")
@login_required
def map_status():
    from app.routes.devices import map_status as legacy

    return legacy()


@bp.get("/batches/<batch_id>")
@login_required
def batch_status(batch_id: str):
    from app.routes.devices import batch_status as legacy

    return legacy(batch_id)


@bp.get("/notifications")
@login_required
def notifications():
    from app.routes.notifications import list_notifications as legacy

    return legacy()


@bp.get("/search/suggest")
@login_required
def search_suggest():
    from app.routes.search import search_suggest as legacy

    return legacy()


@bp.get("/health")
def health_alias():
    return jsonify({"status": "ok"})
