"""Поиск устройств."""

from flask import Blueprint, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app.services.search_service import search_devices, suggest_devices

bp = Blueprint("search", __name__)


def _sector_id_from_args() -> int | None:
    """?sector_id= как число; пустое/нечисло — все доступные."""
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
    """Старый адрес поиска ведёт на карту с полем поиска."""
    args = {}
    query = (request.args.get("q") or "").strip()
    sector_id = _sector_id_from_args()
    if query:
        args["q"] = query
    if sector_id is not None:
        args["sector_id"] = sector_id
    return redirect(url_for("devices.map", **args))


@bp.get("/search/suggest")
@login_required
def suggest():
    query = request.args.get("q") or ""
    return jsonify(suggest_devices(current_user, query))


@bp.get("/search/api")
@login_required
def search_api():
    """JSON для поиска на карте сети (список и фильтр секторов)."""
    query = (request.args.get("q") or "").strip()
    sector_id = _sector_id_from_args()
    devices = search_devices(current_user, query, sector_id=sector_id)
    return jsonify(
        [
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
    )
