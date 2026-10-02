"""Карта сети и карточка устройства."""

from flask import Blueprint, abort, jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.authz import accessible_sectors, get_visible_device_or_404
from app.models import DeviceHistory, Sector
from app.utils import utcnow

bp = Blueprint("devices", __name__)


def _load_visible_sectors():
    """Секторы текущего пользователя с устройствами, отсортированными по IP.

    На карте только машины, которые хотя бы раз отвечали (last_seen).
    Пустые адреса из CIDR в devices больше не создаются.
    Список кладём в sector.visible_devices — relationship не трогаем,
    иначе SQLAlchemy мог бы обнулить sector_id у отфильтрованных строк.
    """
    allowed = accessible_sectors(current_user)
    if not allowed:
        return []

    loaded = (
        Sector.query.options(selectinload(Sector.devices))
        .filter(Sector.id.in_([sector.id for sector in allowed]))
        .all()
    )
    by_id = {sector.id: sector for sector in loaded}
    sectors = [by_id[sector.id] for sector in allowed if sector.id in by_id]
    for sector in sectors:
        visible = [device for device in sector.devices if device.last_seen is not None]
        visible.sort(key=lambda device: device.ip)
        sector.visible_devices = visible
    return sectors


@bp.get("/")
@login_required
def map():
    """Секторы текущего пользователя и устройства в них, по IP."""
    return render_template("devices/map.html", sectors=_load_visible_sectors())


@bp.get("/map/status")
@login_required
def map_status():
    """JSON для автообновления карты: статусы устройств и счётчики секторов."""
    sectors_payload = []
    for sector in _load_visible_sectors():
        devices = [
            {
                "id": device.id,
                "ip": device.ip,
                "hostname": device.hostname,
                "status": device.last_status,
                "kind": device.kind,
            }
            for device in sector.visible_devices
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
    return jsonify(
        {
            "updated_at": utcnow().isoformat(),
            "sectors": sectors_payload,
        }
    )


@bp.get("/devices/<int:device_id>")
@login_required
def detail(device_id: int):
    """Карточка устройства и 20 последних проверок, новые сверху."""
    device = get_visible_device_or_404(device_id)
    if device.sector is None:
        abort(404)
    # Не device.history[:20] — иначе сначала читается вся история.
    history = (
        DeviceHistory.query.filter_by(device_id=device.id)
        .order_by(DeviceHistory.timestamp.desc())
        .limit(20)
        .all()
    )
    return render_template("devices/detail.html", device=device, history=history)
