"""Карта сети и карточка устройства."""

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Device, DeviceHistory, RunType, Script, ScriptRun, Sector
from app.run_display import run_launch_label, run_status_label, run_when_label
from app.services.account_service import device_account_sightings
from app.services.hardware_poll_service import (
    HardwarePollError,
    HardwarePollInProgressError,
    hardware_poll_message,
    load_device_hardware_history,
    poll_one_device,
)
from app.services import batch_service
from app.services.device_kind import hostname_sort_key, kind_counts
from app.services.net_utils import sector_subnet_labels
from app.services.network_summary_service import get_network_summary
from app.services import script_service
from app.services.net_utils import normalize_mac
from app.utils import as_utc, parse_optional_int, utcnow

bp = Blueprint("devices", __name__)

_DETAIL_TABS = frozenset({"overview", "history", "commands", "polls", "hardware"})
_DEFAULT_LIST_LIMIT = 5


def _device_or_404(device_id: int) -> Device:
    device = db.session.get(Device, device_id)
    if device is None:
        abort(404)
    return device


def _devices_by_ids(device_ids) -> list[Device]:
    if not device_ids:
        return []
    ids = [int(device_id) for device_id in device_ids]
    devices_by_id = {
        device.id: device
        for device in Device.query.filter(Device.id.in_(set(ids))).all()
    }
    return [devices_by_id[device_id] for device_id in ids if device_id in devices_by_id]


def _latest_address_per_mac(addresses, primary_ip: str | None):
    """На карточке — один актуальный IP на MAC (последний last_seen).

    Адреса без MAC оставляем как есть: это отдельные наблюдения.
    """
    by_mac: dict[str, object] = {}
    no_mac: list = []
    for row in addresses:
        mac = normalize_mac(getattr(row, "mac", None))
        if not mac:
            no_mac.append(row)
            continue
        prev = by_mac.get(mac)
        if prev is None:
            by_mac[mac] = row
            continue
        prev_ts = as_utc(prev.last_seen).timestamp() if prev.last_seen else 0
        row_ts = as_utc(row.last_seen).timestamp() if row.last_seen else 0
        prev_primary = 1 if prev.ip == primary_ip else 0
        row_primary = 1 if row.ip == primary_ip else 0
        if (row_ts, row_primary, row.id or 0) >= (prev_ts, prev_primary, prev.id or 0):
            by_mac[mac] = row
    return list(by_mac.values()) + no_mac


def _allowed_detail_tabs(device) -> frozenset[str]:
    """Вкладки карточки зависят от типа устройства."""
    tabs = {"overview", "polls", "history"}
    if device.shows_commands:
        tabs.add("commands")
    if device.shows_hardware:
        tabs.add("hardware")
    return frozenset(tabs)


def _detail_tab_for(device) -> str:
    tab = (request.args.get("tab") or "overview").strip().lower()
    if tab == "accounts":
        tab = "history"
    if tab not in _DETAIL_TABS or tab not in _allowed_detail_tabs(device):
        return "overview"
    return tab


def _request_json_dict() -> dict:
    """JSON body as dict; non-JSON requests → {}."""
    if request.is_json:
        payload = request.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}
    return {}


def _parse_device_ids() -> list[int] | None:
    """device_ids из JSON или form. None — поле отсутствует/битое; [] — пустой список.

    Form: повторяемые device_ids / device_ids[] или одно значение через запятую.
    """
    payload = _request_json_dict()
    raw = payload.get("device_ids") if payload else None
    if raw is None and not payload:
        raw_list = request.form.getlist("device_ids") or request.form.getlist("device_ids[]")
        if not raw_list:
            single = request.form.get("device_ids")
            if single is None:
                return None
            raw_list = [single]
        values: list[int] = []
        for item in raw_list:
            for part in str(item).split(","):
                number = parse_optional_int(part)
                if number is None:
                    return None
                values.append(number)
        raw = values

    if raw is None:
        return None
    if isinstance(raw, int):
        raw = [raw]
    if not isinstance(raw, list):
        return None

    values = []
    seen: set[int] = set()
    for item in raw:
        try:
            number = int(item)
        except (TypeError, ValueError):
            return None
        if number in seen:
            continue
        seen.add(number)
        values.append(number)
    return values


def _parse_script_id() -> int | None:
    payload = _request_json_dict()
    if payload:
        return parse_optional_int(
            str(payload["script_id"]) if payload.get("script_id") is not None else None
        )
    return parse_optional_int(request.form.get("script_id"))


def _parse_bulk_device_ids_or_error():
    """(device_ids, None) или (None, (jsonify(...), status))."""
    device_ids = _parse_device_ids()
    if device_ids is None:
        return None, (jsonify({"error": "Укажите device_ids (список id)."}), 400)
    if not device_ids:
        return None, (jsonify({"error": "Список device_ids пуст."}), 400)
    if len(device_ids) > batch_service.MAX_BULK_DEVICES:
        return None, (
            jsonify(
                {"error": f"Не больше {batch_service.MAX_BULK_DEVICES} устройств за раз."}
            ),
            400,
        )
    return device_ids, None


def _bulk_response(batch_id: str, runs: list[ScriptRun], skipped: int):
    return jsonify(
        {
            "batch_id": batch_id,
            "run_ids": [run.id for run in runs],
            "accepted": len(runs),
            "skipped": skipped,
            "progress_url": url_for("scripts.batch_detail", batch_id=batch_id),
        }
    )


def _device_account_fields(device) -> dict:
    """Поля текущей УЗ для карты (только kinds с shows_accounts)."""
    account = device.current_account if device.shows_accounts else None
    if not account:
        return {
            "account_key": None,
            "account_name": None,
            "account_label": None,
        }
    return {
        "account_key": account.account_key,
        "account_name": account.display_name or None,
        "account_label": account.label,
    }


def map_status_payload() -> dict:
    """JSON-контракт /map/status."""
    sectors_payload = []
    for sector in _load_visible_sectors():
        devices = [
            {
                "id": device.id,
                "ip": device.ip,
                "hostname": device.hostname,
                "mac": device.mac,
                "serial": device.serial_number,
                "status": device.last_status,
                "kind": device.kind,
                "url": url_for("devices.detail", device_id=device.id),
                **_device_account_fields(device),
            }
            for device in sector.visible_devices
        ]
        online = sum(1 for device in devices if device["status"] == "online")
        offline = sum(1 for device in devices if device["status"] == "offline")
        unknown = sum(1 for device in devices if device["status"] == "unknown")
        sectors_payload.append(
            {
                "id": sector.id,
                "name": sector.name,
                "online": online,
                "offline": offline,
                "unknown": unknown,
                "total": len(devices),
                "subnets": list(getattr(sector, "subnet_labels", []) or []),
                "kinds": [
                    {"kind": kind, "label": label, "count": count}
                    for kind, label, count in getattr(sector, "kind_stats", [])
                ],
                "devices": devices,
            }
        )
    return {
        "updated_at": utcnow().isoformat(),
        "sectors": sectors_payload,
    }


def _load_visible_sectors():
    """Секторы текущего пользователя с устройствами, отсортированными по имени.

    На карте только машины, которые хотя бы раз отвечали (last_seen).
    Пустые адреса из CIDR в devices больше не создаются.
    Список кладём в sector.visible_devices — relationship не трогаем,
    иначе SQLAlchemy мог бы обнулить sector_id у отфильтрованных строк.
    """
    allowed = Sector.query.order_by(Sector.name).all()
    if not allowed:
        return []

    loaded = (
        Sector.query.options(
            selectinload(Sector.devices).selectinload(Device.current_account),
            selectinload(Sector.ranges),
        )
        .filter(Sector.id.in_([sector.id for sector in allowed]))
        .all()
    )
    by_id = {sector.id: sector for sector in loaded}
    sectors = [by_id[sector.id] for sector in allowed if sector.id in by_id]
    for sector in sectors:
        visible = [device for device in sector.devices if device.last_seen is not None]
        visible.sort(key=lambda device: hostname_sort_key(device.hostname, device.ip))
        sector.visible_devices = visible
        sector.kind_stats = kind_counts(visible)
        sector.subnet_labels = sector_subnet_labels(sector, visible)
    return sectors


@bp.get("/")
@login_required
def map():
    """Секторы текущего пользователя и устройства в них, по IP."""
    sectors = _load_visible_sectors()
    query = (request.args.get("q") or "").strip()
    sector_id = parse_optional_int(request.args.get("sector_id"))
    map_scripts = [
        {"id": script.id, "name": script.name}
        for script in script_service.scripts_visible_to_user(current_user)
    ]
    return render_template(
        "devices/map.html",
        sectors=sectors,
        search_sectors=Sector.query.order_by(Sector.name).all(),
        search_query=query,
        search_sector_id=sector_id,
        map_scripts=map_scripts,
        can_run_diagnostics=True,
    )


@bp.get("/map/status")
@login_required
def map_status():
    """JSON для автообновления карты: статусы устройств и счётчики секторов."""
    return jsonify(map_status_payload())


@bp.post("/api/map/bulk/ping")
@login_required
def bulk_ping():
    """Массовый Ping: один batch_id, start_run(PING) на каждое доступное устройство."""
    device_ids, error = _parse_bulk_device_ids_or_error()
    if error is not None:
        return error

    devices = _devices_by_ids(device_ids)
    skipped = len(device_ids) - len(devices)
    if not devices:
        abort(403)

    batch_id, runs = batch_service.start_bulk_ping(current_user, devices)
    return _bulk_response(batch_id, runs, skipped)


@bp.post("/api/map/bulk/script")
@login_required
def bulk_script():
    """Массовый запуск скрипта библиотеки."""
    device_ids, error = _parse_bulk_device_ids_or_error()
    if error is not None:
        return error

    script_id = _parse_script_id()
    if script_id is None:
        return jsonify({"error": "Укажите script_id."}), 400
    script = db.session.get(Script, script_id)
    if script is None:
        return jsonify({"error": "Скрипт не найден."}), 404
    if script.target_os == "linux" or script.interpreter == "bash":
        return jsonify(
            {
                "error": (
                    "Удалённый Linux в v1 не реализован: "
                    "PsExec работает только с Windows."
                )
            }
        ), 400

    devices = _devices_by_ids(device_ids)
    skipped = len(device_ids) - len(devices)
    if not devices:
        abort(403)

    try:
        batch_id, runs = batch_service.start_bulk_script(script, current_user, devices)
    except script_service.ScriptError as exc:
        return jsonify({"error": str(exc)}), 400
    if not runs:
        return jsonify({"error": "Не удалось запустить скрипт."}), 400
    return _bulk_response(batch_id, runs, skipped)


@bp.get("/api/batches/<batch_id>")
@login_required
def batch_status(batch_id: str):
    """JSON-статус пачки: только runs, видимые через user_can_see_script_run."""
    payload = batch_service.batch_status_payload(current_user, batch_id)
    if payload is None:
        abort(404)
    return jsonify(payload)


@bp.get("/api/network/summary")
@login_required
def network_summary():
    """Сводка сети для карты: счётчики видимых устройств и последний опрос."""
    return jsonify(get_network_summary(current_user))


def _launch_history(
    device_id: int,
    run_types: tuple[str, ...],
    *,
    limit: int,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Запуски на устройстве: (items, total)."""
    base = ScriptRun.query.filter(
        ScriptRun.device_id == device_id,
        ScriptRun.run_type.in_(run_types),
    )
    total = base.count()
    rows = (
        base.options(selectinload(ScriptRun.script))
        .order_by(ScriptRun.started_at.desc(), ScriptRun.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [
        {
            "run": item,
            "label": run_launch_label(item),
            "status_label": run_status_label(item.status),
            "when": run_when_label(item.started_at),
        }
        for item in rows
    ]
    return items, total


@bp.get("/devices/<int:device_id>")
@login_required
def detail(device_id: int):
    """Карточка устройства: вкладки зависят от типа (ноутбук/СБ/сервер/…)."""
    device = _device_or_404(device_id)
    if device.sector is None:
        abort(404)

    tab = _detail_tab_for(device)
    preview_limit = _DEFAULT_LIST_LIMIT

    history = []
    launches = []
    account_rows = []
    hardware_rows = []
    device_batches = []
    if tab == "history":
        launches, _launch_total = _launch_history(
            device.id,
            (RunType.COMMAND, RunType.SCRIPT, RunType.VNC_ENSURE),
            limit=preview_limit,
        )
        device_batches, _batch_total = batch_service.list_batch_summaries(
            current_user,
            limit=preview_limit,
            device_id=device.id,
        )
        history = (
            DeviceHistory.query.filter_by(device_id=device.id)
            .order_by(DeviceHistory.timestamp.desc())
            .limit(preview_limit)
            .all()
        )
        if device.shows_accounts:
            account_rows = device_account_sightings(device.id, limit=preview_limit)
        if device.shows_hardware:
            hardware_rows = load_device_hardware_history(
                device.id, limit=preview_limit
            )

    # Скрипты только если вкладка команд доступна этому типу.
    scripts = (
        script_service.scripts_visible_to_user(current_user)
        if device.shows_commands
        else []
    )
    device_addresses = sorted(
        _latest_address_per_mac(device.addresses, device.ip),
        key=lambda row: (
            0 if row.ip == device.ip else 1,
            -(as_utc(row.last_seen).timestamp() if row.last_seen else 0),
            row.id or 0,
        ),
    )

    from app.live_progress import LIVE_NOTICE_COOKIE, consume_live_notice

    notice, clear_notice = consume_live_notice(device_id=device.id)
    if notice is not None:
        flash(notice[1], notice[0])
    response = make_response(
        render_template(
            "devices/detail.html",
            device=device,
            device_addresses=device_addresses,
            tab=tab,
            list_limit=preview_limit,
            history=history,
            launches=launches,
            device_batches=device_batches,
            account_rows=account_rows,
            hardware_rows=hardware_rows,
            scripts=scripts,
            can_open_batches=True,
            can_run_diagnostics=True,
        )
    )
    if clear_notice:
        response.delete_cookie(LIVE_NOTICE_COOKIE, path="/")
    return response


def _run_hardware_poll(device_id: int, on_progress) -> tuple[str, str]:
    fresh = db.session.get(Device, device_id)
    if fresh is None:
        return "warning", "Устройство не найдено."
    try:
        result = poll_one_device(fresh, on_progress=on_progress)
    except HardwarePollInProgressError as exc:
        return "warning", str(exc)
    except HardwarePollError as exc:
        return "warning", str(exc)
    except Exception as exc:  # noqa: BLE001
        return "danger", f"Ошибка опроса железа: {exc}"
    db.session.refresh(fresh)
    return hardware_poll_message(fresh, result)


@bp.post("/devices/<int:device_id>/hardware-poll")
@login_required
def poll_device_hardware(device_id: int):
    """Разовый опрос железа этой машины через bAD."""
    from app.live_progress import stream_live_progress, wants_live_progress

    device = _device_or_404(device_id)
    if not device.shows_hardware:
        abort(404)
    checked_id = device.id
    db.session.rollback()
    redirect_url = url_for("devices.detail", device_id=checked_id, tab="hardware")
    if wants_live_progress():
        return stream_live_progress(
            checked_id,
            redirect_url,
            lambda on_progress: _run_hardware_poll(checked_id, on_progress),
            first_step="Опрашиваю железо…",
        )
    category, message = _run_hardware_poll(checked_id, None)
    flash(message, category)
    return redirect(redirect_url)


@bp.post("/devices/<int:device_id>/scripts/run")
@login_required
def run_script_on_device(device_id: int):
    """Быстрый запуск скрипта с карточки устройства → detail первого run."""
    device = _device_or_404(device_id)
    if not device.shows_commands:
        abort(404)
    script_id = parse_optional_int(request.form.get("script_id"))
    if script_id is None:
        flash("Выберите скрипт.", "warning")
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))

    script = db.session.get(Script, script_id)
    if script is None:
        flash("Скрипт не найден.", "danger")
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))
    if script.target_os == "linux" or script.interpreter == "bash":
        flash(
            "Удалённый Linux в v1 не реализован: PsExec работает только с Windows.",
            "danger",
        )
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))

    try:
        run_as = script_service.parse_run_as(request.form.get("run_as"))
        runs = script_service.start_script_on_devices(
            script, current_user, [device], run_as=run_as
        )
    except script_service.ScriptError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))
    if not runs:
        flash("Не удалось запустить скрипт.", "danger")
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))
    return redirect(url_for("scripts.run_detail", run_id=runs[0].id))
