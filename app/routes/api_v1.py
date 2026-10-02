"""Внутренний API v1: aliases на существующие JSON-эндпоинты.

Старые пути не удаляем — только добавляем `/api/v1/...` с тем же JSON.
"""

from flask import Blueprint, abort, jsonify, request, url_for
from flask_login import current_user, login_required

from app.authz import admin_required, accessible_sectors
from app.services.batch_service import batch_status_payload
from app.services.command_presets import list_command_presets
from app.services.network_summary_service import get_network_summary
from app.services.notification_service import list_for_user
from app.services.search_service import search_devices
from app.utils import parse_optional_int, utcnow
from sqlalchemy.orm import selectinload

from app.models import Sector

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def _map_status_payload():
    """Тот же контракт, что devices.map_status (без HTTP-зависимости)."""
    allowed = accessible_sectors(current_user)
    if not allowed:
        return {"updated_at": utcnow().isoformat(), "sectors": []}
    loaded = (
        Sector.query.options(selectinload(Sector.devices))
        .filter(Sector.id.in_([sector.id for sector in allowed]))
        .all()
    )
    by_id = {sector.id: sector for sector in loaded}
    sectors_payload = []
    for sector in allowed:
        row = by_id.get(sector.id)
        if row is None:
            continue
        visible = [d for d in row.devices if d.last_seen is not None]
        visible.sort(key=lambda device: device.ip)
        devices = [
            {
                "id": device.id,
                "ip": device.ip,
                "hostname": device.hostname,
                "status": device.last_status,
                "kind": device.kind,
                "url": url_for("devices.detail", device_id=device.id),
            }
            for device in visible
        ]
        online = sum(1 for device in devices if device["status"] == "online")
        sectors_payload.append(
            {
                "id": sector.id,
                "name": sector.name,
                "online": online,
                "total": len(devices),
                "devices": devices,
            }
        )
    return {"updated_at": utcnow().isoformat(), "sectors": sectors_payload}


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
    return jsonify(_map_status_payload())


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
    query = (request.args.get("q") or "").strip()
    sector_id = parse_optional_int(request.args.get("sector_id"))
    if len(query) < 2:
        return jsonify({"query": query, "results": []})
    devices = search_devices(current_user, query, sector_id=sector_id)
    results = [
        {
            "id": device.id,
            "hostname": device.hostname,
            "ip": device.ip,
            "status": device.last_status,
            "url": url_for("devices.detail", device_id=device.id),
        }
        for device in devices
    ]
    return jsonify({"query": query, "results": results, "updated_at": utcnow().isoformat()})


@bp.get("/health")
def health_alias():
    return jsonify({"status": "ok"})
