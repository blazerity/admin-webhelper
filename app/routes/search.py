"""Поиск устройств."""

from flask import Blueprint, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app.services.search_service import search_devices
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


def _search_json_payload():
    """Общий JSON для /search/api и /search/suggest."""
    query = (request.args.get("q") or "").strip()
    sector_id = parse_optional_int(request.args.get("sector_id"))
    devices = search_devices(current_user, query, sector_id=sector_id)
    return [
        {
            "id": device.id,
            "ip": device.ip,
            "hostname": device.hostname,
            "mac": device.mac,
            "status": device.last_status,
            "kind": device.kind,
            "sector": device.sector.name if device.sector else "",
            "url": url_for("devices.detail", device_id=device.id),
        }
        for device in devices
    ]


@bp.get("/search/api")
@login_required
def search_api():
    """JSON для поиска на карте сети (список и фильтр секторов)."""
    return jsonify(_search_json_payload())


@bp.get("/search/suggest")
@login_required
def search_suggest():
    """Autocomplete на карте (тот же payload, что /search/api)."""
    return jsonify(_search_json_payload())
