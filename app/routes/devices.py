"""Карта сети и карточка устройства.

Эндпоинты:
- map      GET /
- detail   GET /devices/<device_id>
"""

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.authz import accessible_sectors, get_visible_device_or_404
from app.models import DeviceHistory, Sector

bp = Blueprint("devices", __name__)


@bp.get("/")
@login_required
def map():
    """Секторы текущего пользователя и устройства в них, по IP."""
    allowed = accessible_sectors(current_user)
    if not allowed:
        return render_template("devices/map.html", sectors=[])

    # accessible_sectors уже отфильтровал права. selectinload забирает
    # устройства всех этих секторов одним запросом: без него шаблон
    # сходил бы в базу отдельно за каждым сектором (N+1).
    loaded = (
        Sector.query.options(selectinload(Sector.devices))
        .filter(Sector.id.in_([sector.id for sector in allowed]))
        .all()
    )
    by_id = {sector.id: sector for sector in loaded}
    sectors = [by_id[sector.id] for sector in allowed if sector.id in by_id]
    for sector in sectors:
        sector.devices.sort(key=lambda device: device.ip)
    return render_template("devices/map.html", sectors=sectors)


@bp.get("/devices/<int:device_id>")
@login_required
def detail(device_id: int):
    """Карточка устройства и 20 последних проверок, новые сверху."""
    device = get_visible_device_or_404(device_id)
    # Шаблон печатает device.sector.name. Чужой сектор сюда не попадёт:
    # get_visible_device_or_404 уже вернул 403 или 404.
    if device.sector is None:
        abort(404)
    # Не device.history[:20]: так сначала прочитается вся история.
    # limit оставляет в ответе только 20 самых новых строк.
    history = (
        DeviceHistory.query.filter_by(device_id=device.id)
        .order_by(DeviceHistory.timestamp.desc())
        .limit(20)
        .all()
    )
    return render_template("devices/detail.html", device=device, history=history)
