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

_DETAIL_TABS = frozenset({"overview", "commands", "polls"})
_DEFAULT_LIST_LIMIT = 5
_ALL_LIST_LIMIT = 50


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


def _list_limit() -> tuple[int, bool]:
    """(limit, show_all) — по умолчанию 5, при all=1 — расширенный список."""
    show_all = (request.args.get("all") or "").strip() in {"1", "true", "yes"}
    return (_ALL_LIST_LIMIT if show_all else _DEFAULT_LIST_LIMIT, show_all)


def _detail_tab() -> str:
    tab = (request.args.get("tab") or "overview").strip().lower()
    if tab not in _DETAIL_TABS:
        return "overview"
    return tab


def _launch_history(
    device_id: int, run_types: tuple[str, ...], limit: int
) -> tuple[list[dict], bool]:
    """Последние запуски на устройстве заданных типов.

    Возвращает (items, has_more). При урезанном списке читаем limit+1,
    чтобы понять, есть ли ещё записи для кнопки «Показать все».
    """
    rows = (
        ScriptRun.query.options(selectinload(ScriptRun.script))
        .filter(
            ScriptRun.device_id == device_id,
            ScriptRun.run_type.in_(run_types),
        )
        .order_by(ScriptRun.started_at.desc(), ScriptRun.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    items = [
        {
            "run": item,
            "label": _launch_label(item),
            "status_label": _RUN_STATUS_LABELS.get(item.status, item.status),
            "when": _format_run_when(item.started_at),
        }
        for item in rows[:limit]
    ]
    return items, has_more


@bp.get("/devices/<int:device_id>")
@login_required
def detail(device_id: int):
    """Карточка устройства: вкладки overview / commands / polls."""
    device = get_visible_device_or_404(device_id)
    if device.sector is None:
        abort(404)

    tab = _detail_tab()
    limit, show_all = _list_limit()

    history = []
    launches = []
    has_more = False
    if tab == "polls":
        rows = (
            DeviceHistory.query.filter_by(device_id=device.id)
            .order_by(DeviceHistory.timestamp.desc())
            .limit(limit + 1)
            .all()
        )
        has_more = (not show_all) and len(rows) > limit
        history = rows[:limit]
    elif tab == "commands":
        launches, more = _launch_history(
            device.id,
            (RunType.COMMAND, RunType.SCRIPT),
            limit,
        )
        has_more = (not show_all) and more

    return render_template(
        "devices/detail.html",
        device=device,
        tab=tab,
        show_all=show_all,
        has_more=has_more,
        list_limit=limit,
        history=history,
        launches=launches,
    )
