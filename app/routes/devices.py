"""Карта сети и карточка устройства."""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.authz import (
    accessible_sectors,
    admin_required,
    filter_accessible_devices,
    get_visible_device_or_404,
    user_can_connect_vnc,
    user_can_run_diagnostics,
    user_can_run_script,
    user_can_run_scripts,
)
from app.extensions import db
from app.models import Device, DeviceHistory, RunType, Script, ScriptRun, Sector
from app.run_display import run_launch_label, run_status_label, run_when_label
from app.services.account_service import (
    count_device_account_sightings,
    device_account_sightings,
)
from app.services.hardware_poll_service import (
    HardwarePollError,
    HardwarePollInProgressError,
    count_device_hardware_history,
    load_device_hardware_history,
    poll_one_device,
)
from app.services import batch_service
from app.services.command_presets import list_command_presets
from app.services.device_kind import hostname_sort_key, kind_counts
from app.services.net_utils import sector_subnet_labels
from app.services.network_summary_service import get_network_summary
from app.services import script_service
from app.utils import normalize_page, parse_optional_int, utcnow

bp = Blueprint("devices", __name__)

_DETAIL_TABS = frozenset({"overview", "accounts", "commands", "polls", "hardware"})
_DEFAULT_LIST_LIMIT = 5
_HISTORY_PER_PAGE = 20


def _allowed_detail_tabs(device) -> frozenset[str]:
    """Вкладки карточки зависят от типа устройства."""
    tabs = {"overview", "polls"}
    if device.shows_accounts:
        tabs.add("accounts")
    if device.shows_commands:
        tabs.add("commands")
    if device.shows_hardware:
        tabs.add("hardware")
    return frozenset(tabs)


def _detail_tab_for(device) -> str:
    tab = (request.args.get("tab") or "overview").strip().lower()
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
    allowed = accessible_sectors(current_user)
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
    map_scripts = []
    if user_can_run_scripts(current_user):
        map_scripts = [
            {"id": script.id, "name": script.name}
            for script in script_service.scripts_visible_to_user(current_user)
        ]
    return render_template(
        "devices/map.html",
        sectors=sectors,
        search_sectors=accessible_sectors(current_user),
        search_query=query,
        search_sector_id=sector_id,
        map_scripts=map_scripts,
        can_run_diagnostics=user_can_run_diagnostics(current_user),
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
    if not user_can_run_diagnostics(current_user):
        abort(403)
    device_ids, error = _parse_bulk_device_ids_or_error()
    if error is not None:
        return error

    devices = filter_accessible_devices(current_user, device_ids)
    skipped = len(device_ids) - len(devices)
    if not devices:
        abort(403)

    batch_id, runs = batch_service.start_bulk_ping(current_user, devices)
    return _bulk_response(batch_id, runs, skipped)


@bp.post("/api/map/bulk/script")
@login_required
def bulk_script():
    """Массовый запуск скрипта библиотеки (user_can_run_scripts + ACL скрипта)."""
    if not user_can_run_scripts(current_user):
        abort(403)

    device_ids, error = _parse_bulk_device_ids_or_error()
    if error is not None:
        return error

    script_id = _parse_script_id()
    if script_id is None:
        return jsonify({"error": "Укажите script_id."}), 400
    script = db.session.get(Script, script_id)
    if script is None:
        return jsonify({"error": "Скрипт не найден."}), 404
    if not user_can_run_script(current_user, script):
        abort(403)
    if script.target_os == "linux" or script.interpreter == "bash":
        return jsonify(
            {
                "error": (
                    "Удалённый Linux в v1 не реализован: "
                    "PsExec работает только с Windows."
                )
            }
        ), 400

    devices = filter_accessible_devices(current_user, device_ids)
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


@bp.get("/api/command-presets")
@admin_required
def command_presets_api():
    """JSON пресетов удалённых команд (источник истины — services.command_presets)."""
    return jsonify({"presets": list_command_presets()})


def _history_paging() -> tuple[bool, int, int, int]:
    """(show_all, page, per_page, offset) для журналов карточки устройства.

    По умолчанию — 5 последних. При all=1 — постраничный просмотр.
    """
    show_all = (request.args.get("all") or "").strip() in {"1", "true", "yes"}
    if not show_all:
        return False, 1, _DEFAULT_LIST_LIMIT, 0
    page, per_page = normalize_page(
        parse_optional_int(request.args.get("page")),
        parse_optional_int(request.args.get("per_page")) or _HISTORY_PER_PAGE,
        max_per_page=100,
    )
    return True, page, per_page, (page - 1) * per_page


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
    device = get_visible_device_or_404(device_id)
    if device.sector is None:
        abort(404)

    tab = _detail_tab_for(device)
    show_all, page, per_page, offset = _history_paging()
    preview_limit = _DEFAULT_LIST_LIMIT

    history = []
    launches = []
    account_rows = []
    hardware_rows = []
    has_more = False
    list_total = 0
    if tab == "polls":
        list_total = DeviceHistory.query.filter_by(device_id=device.id).count()
        if show_all:
            history = (
                DeviceHistory.query.filter_by(device_id=device.id)
                .order_by(DeviceHistory.timestamp.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )
        else:
            history = (
                DeviceHistory.query.filter_by(device_id=device.id)
                .order_by(DeviceHistory.timestamp.desc())
                .limit(preview_limit)
                .all()
            )
            has_more = list_total > preview_limit
    elif tab == "commands" and device.shows_commands:
        if show_all:
            launches, list_total = _launch_history(
                device.id,
                (RunType.COMMAND, RunType.SCRIPT, RunType.VNC_ENSURE),
                limit=per_page,
                offset=offset,
            )
        else:
            launches, list_total = _launch_history(
                device.id,
                (RunType.COMMAND, RunType.SCRIPT, RunType.VNC_ENSURE),
                limit=preview_limit,
            )
            has_more = list_total > preview_limit
    elif tab == "accounts" and device.shows_accounts:
        list_total = count_device_account_sightings(device.id)
        if show_all:
            account_rows = device_account_sightings(
                device.id, limit=per_page, offset=offset
            )
        else:
            account_rows = device_account_sightings(device.id, limit=preview_limit)
            has_more = list_total > preview_limit
    elif tab == "hardware" and device.shows_hardware:
        list_total = count_device_hardware_history(device.id)
        if show_all:
            hardware_rows = load_device_hardware_history(
                device.id, limit=per_page, offset=offset
            )
        else:
            hardware_rows = load_device_hardware_history(
                device.id, limit=preview_limit
            )
            has_more = list_total > preview_limit

    # Пресеты/скрипты только если вкладка команд доступна этому типу.
    command_presets = list_command_presets() if device.shows_commands else []
    scripts = (
        script_service.scripts_visible_to_user(current_user)
        if device.shows_commands and user_can_run_scripts(current_user)
        else []
    )

    return render_template(
        "devices/detail.html",
        device=device,
        tab=tab,
        show_all=show_all,
        has_more=has_more,
        list_limit=preview_limit if not show_all else per_page,
        list_page=page,
        list_per_page=per_page,
        list_total=list_total,
        history=history,
        launches=launches,
        account_rows=account_rows,
        hardware_rows=hardware_rows,
        command_presets=command_presets,
        scripts=scripts,
        can_run_diagnostics=user_can_run_diagnostics(current_user),
        can_connect_vnc=user_can_connect_vnc(current_user) and device.shows_commands,
    )


@bp.get("/devices/<int:device_id>/vnc")
@login_required
def vnc_session(device_id: int):
    """Экспериментальный стол в браузере (noVNC через Debian)."""
    device = get_visible_device_or_404(device_id)
    if device.sector is None:
        abort(404)
    if not user_can_connect_vnc(current_user):
        abort(403)
    if not device.shows_commands:
        abort(404)

    from app.services.vnc_settings import get_vnc_settings

    settings = get_vnc_settings(include_password=True)
    return render_template(
        "devices/vnc.html",
        device=device,
        vnc_port=settings.port,
        vnc_password=settings.password,
        has_vnc_password=settings.password_set,
        gateway_enabled=settings.gateway_enabled,
        prepare_url=url_for("devices.vnc_prepare", device_id=device.id),
        ticket_url=url_for("devices.vnc_ticket", device_id=device.id),
    )


@bp.post("/devices/<int:device_id>/vnc/prepare")
@login_required
def vnc_prepare(device_id: int):
    """Поставить или обновить TightVNC на ПК, затем клиент возьмёт билет."""
    device = get_visible_device_or_404(device_id)
    if device.sector is None or not device.shows_commands:
        abort(404)
    if not user_can_connect_vnc(current_user):
        abort(403)

    from app.services.script_service import ScriptError, start_vnc_ensure
    from app.services.tightvnc_install_script import TightVncEnsureError
    from app.services.vnc_settings import get_vnc_settings

    payload = request.get_json(silent=True) or {}
    typed = str(payload.get("password") or "").strip()
    settings = get_vnc_settings(include_password=True)
    password = typed or settings.password
    try:
        run = start_vnc_ensure(current_user, device, password)
    except (TightVncEnsureError, ScriptError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "Не удалось запустить установку агента VNC."}), 400
    return jsonify(
        {
            "run_id": run.id,
            "status_url": url_for("scripts.run_status", run_id=run.id),
            "ticket_url": url_for("devices.vnc_ticket", device_id=device.id),
        }
    )


@bp.get("/devices/<int:device_id>/vnc/ticket")
@login_required
def vnc_ticket(device_id: int):
    """Билет на шлюз после того, как агент на ПК готов."""
    device = get_visible_device_or_404(device_id)
    if device.sector is None or not device.shows_commands:
        abort(404)
    if not user_can_connect_vnc(current_user):
        abort(403)

    from app.services.vnc_settings import get_vnc_settings
    from app.services.vnc_token import VncTokenError, mint_ticket

    settings = get_vnc_settings(include_password=False)
    try:
        token = mint_ticket(
            device_id=device.id,
            user_id=current_user.id,
            ip=device.ip,
            port=settings.port,
        )
    except VncTokenError as exc:
        return jsonify({"error": str(exc)}), 400
    ws_scheme = "wss" if request.is_secure else "ws"
    return jsonify({"ws_url": f"{ws_scheme}://{request.host}/vnc/ws?token={token}"})


@bp.post("/devices/<int:device_id>/hardware-poll")
@admin_required
def poll_device_hardware(device_id: int):
    """Разовый WMI-опрос железа этой машины (admin)."""
    device = get_visible_device_or_404(device_id)
    if not device.shows_hardware:
        abort(404)
    try:
        result = poll_one_device(device)
    except HardwarePollInProgressError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("devices.detail", device_id=device.id, tab="hardware"))
    except HardwarePollError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("devices.detail", device_id=device.id, tab="hardware"))
    except Exception as exc:  # noqa: BLE001
        flash(f"Ошибка опроса железа: {exc}", "danger")
        return redirect(url_for("devices.detail", device_id=device.id, tab="hardware"))

    db.session.refresh(device)
    if result.mismatched:
        flash(
            "На этом IP сейчас другая машина (другой серийный номер или имя). "
            "Снимок железа не записан, чтобы не смешать конфигурации.",
            "warning",
        )
    elif result.collected:
        ram = f"{device.ram_gb} ГБ" if device.ram_gb is not None else "—"
        disk = f"{device.disk_gb} ГБ" if device.disk_gb is not None else "—"
        flash(
            f"Железо обновлено: {device.os_label or '—'} · {device.cpu_name or '—'} "
            f"· ОЗУ {ram} · диски {disk}.",
            "success",
        )
    elif result.offline:
        flash("Устройство не ответило на ping — железо не читали.", "warning")
    elif result.no_wmi:
        flash("WMI не отдал конфигурацию железа.", "warning")
    else:
        flash(
            "Опрос железа: проверено {scanned}, ошибок {errors}.".format(**result.as_dict()),
            "warning",
        )
    return redirect(url_for("devices.detail", device_id=device.id, tab="hardware"))


@bp.post("/devices/<int:device_id>/scripts/run")
@login_required
def run_script_on_device(device_id: int):
    """Быстрый запуск скрипта с карточки устройства → detail первого run."""
    device = get_visible_device_or_404(device_id)
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
    if not user_can_run_script(current_user, script):
        abort(403)
    if script.target_os == "linux" or script.interpreter == "bash":
        flash(
            "Удалённый Linux в v1 не реализован: PsExec работает только с Windows.",
            "danger",
        )
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))

    try:
        runs = script_service.start_script_on_devices(script, current_user, [device])
    except script_service.ScriptError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))
    if not runs:
        flash("Не удалось запустить скрипт.", "danger")
        return redirect(url_for("devices.detail", device_id=device.id, tab="commands"))
    return redirect(url_for("scripts.run_detail", run_id=runs[0].id))
