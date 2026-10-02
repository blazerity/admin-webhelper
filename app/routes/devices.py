"""Карта сети и карточка устройства."""

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.authz import accessible_sectors, get_visible_device_or_404
from app.models import DeviceHistory, RunStatus, RunType, ScriptRun, Sector
from app.services.search_service import search_devices
from app.utils import utcnow

bp = Blueprint("devices", __name__)

_RUN_STATUS_LABELS = {
    RunStatus.PENDING: "ожидание",
    RunStatus.RUNNING: "выполняется",
    RunStatus.SUCCESS: "успешно",
    RunStatus.FAILED: "ошибка",
    RunStatus.CANCELLED: "остановлен",
}

_RUN_TYPE_LABELS = {
    RunType.PING: "Ping",
    RunType.TRACERT: "Трассировка",
    RunType.COMMAND: "Команда",
    RunType.SCRIPT: "Скрипт",
}


def _load_visible_sectors():
    """Секторы текущего пользователя с устройствами, отсортированными по IP."""
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
        sector.devices.sort(key=lambda device: device.ip)
    return sectors


def _sector_id_from_args() -> int | None:
    raw = (request.args.get("sector_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@bp.get("/")
@login_required
def map():
    """Секторы текущего пользователя и устройства в них, по IP."""
    sectors = _load_visible_sectors()
    query = (request.args.get("q") or "").strip()
    sector_id = _sector_id_from_args()
    search_results = (
        search_devices(current_user, query, sector_id=sector_id) if query else []
    )
    return render_template(
        "devices/map.html",
        sectors=sectors,
        search_sectors=accessible_sectors(current_user),
        search_query=query,
        search_sector_id=sector_id,
        search_results=search_results,
    )


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
            for device in sector.devices
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


def _format_run_when(value) -> str:
    if not value:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _launch_label(item: ScriptRun) -> str:
    if item.script is not None and item.script.name:
        return item.script.name
    text = " ".join((item.command_text or "").split())
    if len(text) > 72:
        text = text[:72] + "…"
    kind = _RUN_TYPE_LABELS.get(item.run_type, item.run_type)
    if text:
        return f"{kind}: {text}"
    return kind


def _launch_history(device_id: int) -> list[dict]:
    """Последние запуски на устройстве: ping, tracert, команды и скрипты."""
    rows = (
        ScriptRun.query.options(selectinload(ScriptRun.script))
        .filter(ScriptRun.device_id == device_id)
        .order_by(ScriptRun.started_at.desc(), ScriptRun.id.desc())
        .limit(30)
        .all()
    )
    return [
        {
            "run": item,
            "label": _launch_label(item),
            "status_label": _RUN_STATUS_LABELS.get(item.status, item.status),
            "when": _format_run_when(item.started_at),
        }
        for item in rows
    ]


@bp.get("/devices/<int:device_id>")
@login_required
def detail(device_id: int):
    """Карточка устройства: история слева, действия справа."""
    device = get_visible_device_or_404(device_id)
    if device.sector is None:
        abort(404)
    history = (
        DeviceHistory.query.filter_by(device_id=device.id)
        .order_by(DeviceHistory.timestamp.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "devices/detail.html",
        device=device,
        history=history,
        launches=_launch_history(device.id),
    )
