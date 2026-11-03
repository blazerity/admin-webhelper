"""Идентичность устройств: адреса интерфейсов и слияние «призраков».

Паспорт машины — только ``serial_number``. IP/MAC — атрибуты сетевых
интерфейсов (их может быть несколько: Ethernet + Wi‑Fi). Призрак без SN
на старом IP после появления SN на новом — сливаем в карточку с SN,
если совпал IP или WMI-hostname.

Имя компьютера паспортом не является. PTR часто общий на несколько
адресов и отстаёт, когда VPN отдаёт адрес другой машине. Короткое имя
можно переименовать. Читать серийник и читать имя с самой машины —
один и тот же WMI под админской учёткой; имя из DNS этого доступа
не требует и как раз поэтому не доказывает, кто ответил на пинг.

``confirm_device_address`` — единственная проверка перед командой,
скриптом, ping/tracert и билетом VNC. У карточки с серийником молчание
последнего IP ещё не значит, что машина недоступна: ищем тот же
серийник на прежних адресах карточки и по DNS-имени. Полный обход
диапазонов сектора остаётся опросу сети. Без серийника карточка и
есть адрес, хватает ICMP.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Device, DeviceAccountHistory, DeviceHardwareHistory, DeviceHistory
from app.models.device import DeviceAddress, DeviceStatus
from app.models.script import ScriptRun
from app.services import discovery_service
from app.services.net_utils import normalize_mac
from app.utils import utcnow

logger = logging.getLogger(__name__)


def upsert_device_address(
    device: Device,
    *,
    ip: str,
    mac: str | None,
    status: str,
) -> DeviceAddress:
    """Записать/обновить адрес интерфейса. ``devices.ip`` — отдельно (основной)."""
    ip = (ip or "").strip()
    if not ip:
        raise ValueError("IP адреса обязателен.")
    row = db.session.scalars(
        select(DeviceAddress).where(
            DeviceAddress.device_id == device.id,
            DeviceAddress.ip == ip,
        )
    ).first()
    now = utcnow()
    if row is None:
        row = DeviceAddress(device_id=device.id, ip=ip)
        db.session.add(row)
    row.last_status = status
    if status == DeviceStatus.ONLINE:
        row.last_seen = now
    cleaned_mac = normalize_mac(mac)
    if cleaned_mac:
        row.mac = cleaned_mac
    return row


def mark_address_offline(ip: str) -> list[Device]:
    """Пометить адрес offline; вернуть затронутые карточки для пересчёта статуса."""
    ip = (ip or "").strip()
    if not ip:
        return []
    now = utcnow()
    rows = list(db.session.scalars(select(DeviceAddress).where(DeviceAddress.ip == ip)))
    touched_ids: set[int] = {row.device_id for row in rows}
    for row in rows:
        if row.last_status != DeviceStatus.OFFLINE:
            row.last_status = DeviceStatus.OFFLINE

    by_id: dict[int, Device] = {}
    if touched_ids:
        by_id = {
            device.id: device
            for device in db.session.scalars(
                select(Device)
                .options(selectinload(Device.addresses))
                .where(Device.id.in_(list(touched_ids)))
            )
        }
    # Карточки, у которых этот IP ещё основной, но строки адреса не было.
    for device in db.session.scalars(
        select(Device)
        .options(selectinload(Device.addresses))
        .where(Device.ip == ip)
    ):
        by_id.setdefault(device.id, device)

    devices = list(by_id.values())
    for device in devices:
        _refresh_primary_after_offline(device, offline_ip=ip, when=now)
    return devices


def _refresh_primary_after_offline(device: Device, *, offline_ip: str, when) -> None:
    """Если основной IP ушёл в offline — взять другой онлайн-адрес или пометить offline."""
    others = [
        row
        for row in device.addresses
        if row.ip != offline_ip and row.last_status == DeviceStatus.ONLINE
    ]
    if others:
        best = max(others, key=lambda row: (row.last_seen or when, row.id))
        device.ip = best.ip
        if best.mac:
            device.mac = best.mac
        device.last_status = DeviceStatus.ONLINE
        device.last_seen = best.last_seen or device.last_seen
        return
    if device.ip == offline_ip:
        device.last_status = DeviceStatus.OFFLINE
        device.last_response_time_ms = None


def absorb_ghosts(
    keeper: Device,
    *,
    ip: str | None,
    hostname: str | None,
    hostname_trusted: bool,
) -> list[int]:
    """Слить призраков без SN в карточку с серийником.

    Условия (достаточно одного):
    * тот же IP и пустой SN;
    * совпал hostname (только если имя с WMI, не PTR) и пустой SN.

    История опросов/УЗ/железа переносится на keeper, затем призрак удаляется.
    """
    if not keeper.id:
        return []
    if not discovery_service.normalize_serial(keeper.serial_number):
        return []

    candidates: dict[int, Device] = {}
    if ip:
        for ghost in db.session.scalars(
            select(Device).where(Device.ip == ip, Device.id != keeper.id)
        ):
            if discovery_service.normalize_serial(ghost.serial_number):
                continue
            candidates[ghost.id] = ghost

    if hostname_trusted and hostname:
        key = discovery_service.hostname_key(hostname)
        if key:
            host_lower = func.lower(Device.hostname)
            for ghost in db.session.scalars(
                select(Device).where(
                    Device.id != keeper.id,
                    Device.hostname.is_not(None),
                    or_(host_lower == key, host_lower.like(f"{key}.%")),
                )
            ):
                if discovery_service.normalize_serial(ghost.serial_number):
                    continue
                if discovery_service.hostnames_match(ghost.hostname, hostname):
                    candidates[ghost.id] = ghost

    absorbed: list[int] = []
    for ghost in list(candidates.values()):
        ghost_id = ghost.id
        try:
            with db.session.begin_nested():
                _merge_ghost_into(keeper, ghost)
            absorbed.append(ghost_id)
        except Exception:
            logger.exception(
                "Не удалось слить призрак id=%s в id=%s", ghost_id, keeper.id
            )
    if absorbed:
        logger.info(
            "Слиты призраки %s → устройство id=%s (sn=%s)",
            absorbed,
            keeper.id,
            keeper.serial_number,
        )
    return absorbed


def _merge_ghost_into(keeper: Device, ghost: Device) -> None:
    """Перенести связанные строки и адреса, удалить призрак."""
    ghost_id = ghost.id
    keeper_id = keeper.id

    keeper_addrs = {
        row.ip: row
        for row in db.session.scalars(
            select(DeviceAddress).where(DeviceAddress.device_id == keeper_id)
        )
    }
    for addr in list(
        db.session.scalars(
            select(DeviceAddress).where(DeviceAddress.device_id == ghost_id)
        )
    ):
        existing = keeper_addrs.get(addr.ip)
        if existing is None:
            addr.device_id = keeper_id
            keeper_addrs[addr.ip] = addr
        else:
            if addr.last_seen and (
                existing.last_seen is None or addr.last_seen > existing.last_seen
            ):
                existing.last_seen = addr.last_seen
                existing.last_status = addr.last_status
            if addr.mac and not existing.mac:
                existing.mac = addr.mac
            db.session.delete(addr)

    # Также зафиксировать основной IP призрака как адрес keeper.
    if ghost.ip:
        upsert_device_address(
            keeper,
            ip=ghost.ip,
            mac=ghost.mac,
            status=ghost.last_status or DeviceStatus.OFFLINE,
        )

    db.session.execute(
        update(DeviceHistory)
        .where(DeviceHistory.device_id == ghost_id)
        .values(device_id=keeper_id)
    )
    db.session.execute(
        update(DeviceAccountHistory)
        .where(DeviceAccountHistory.device_id == ghost_id)
        .values(device_id=keeper_id)
    )
    db.session.execute(
        update(DeviceHardwareHistory)
        .where(DeviceHardwareHistory.device_id == ghost_id)
        .values(device_id=keeper_id)
    )
    db.session.execute(
        update(ScriptRun)
        .where(ScriptRun.device_id == ghost_id)
        .values(device_id=keeper_id)
    )

    if not keeper.hostname and ghost.hostname:
        keeper.hostname = ghost.hostname
    if not keeper.mac and ghost.mac:
        keeper.mac = ghost.mac
    if keeper.hardware_checked_at is None and ghost.hardware_checked_at is not None:
        keeper.cpu_name = ghost.cpu_name
        keeper.ram_gb = ghost.ram_gb
        keeper.disk_gb = ghost.disk_gb
        keeper.os_caption = ghost.os_caption
        keeper.os_family = ghost.os_family
        keeper.os_edition = ghost.os_edition
        keeper.os_display_version = ghost.os_display_version
        keeper.os_build = ghost.os_build
        keeper.hardware_checked_at = ghost.hardware_checked_at

    db.session.delete(ghost)
    db.session.flush()


class DeviceTargetError(Exception):
    """Адрес карточки сейчас нельзя считать этой машиной."""


def machine_matches_card(stored_serial: str | None, live_serial: str | None) -> str:
    """Как живой серийник относится к паспорту карточки.

    ``no_serial`` — у карточки нет паспорта, адрес и есть устройство.
    ``match`` / ``mismatch`` — оба серийника прочитаны.
    ``unproven`` — паспорт есть, а с адреса серийник не пришёл.
    Имя машины в решение не входит.
    """
    stored = discovery_service.normalize_serial(stored_serial)
    live = discovery_service.normalize_serial(live_serial)
    if not stored:
        return "no_serial"
    if not live:
        return "unproven"
    if stored == live:
        return "match"
    return "mismatch"


def confirm_device_address(
    device: Device,
    *,
    user_id: int | None = None,
    timeout_s: int = 1,
) -> str:
    """Вернуть IP, на котором сейчас отвечает эта карточка.

    С серийником перебираем последний адрес, прежние адреса карточки
    и DNS-имя. Совпадение серийника переносит основной IP. Пишет статус
    и строку истории и делает commit до возврата или исключения.
    """
    if device is None or not getattr(device, "id", None):
        raise DeviceTargetError("Устройство не задано.")

    has_serial = bool(discovery_service.normalize_serial(device.serial_number))
    creds = _wmi_creds_for_target(user_id) if has_serial else None
    tried: list[str] = []
    stranger: tuple[str, str | None] | None = None
    saw_unproven = False
    for ip in _candidate_ips(device):
        tried.append(ip)
        hit = _probe_candidate(device, ip, creds=creds, timeout_s=timeout_s)
        if hit.verdict in {"match", "no_serial"}:
            if ip != (device.ip or "").strip():
                logger.info(
                    "Устройство id=%s найдено на %s вместо %s",
                    device.id,
                    ip,
                    device.ip,
                )
                _move_primary_ip(device, ip, hit.mac)
            _remember_endpoint(device, DeviceStatus.ONLINE, hit.response_time_ms)
            db.session.commit()
            return ip
        if hit.verdict == "mismatch" and stranger is None:
            stranger = (ip, hit.live_name)
        if hit.verdict == "unproven":
            saw_unproven = True

    _remember_endpoint(device, DeviceStatus.OFFLINE, None)
    db.session.commit()
    raise DeviceTargetError(
        _not_found_text(
            device,
            tried,
            stranger=stranger,
            creds_missing=has_serial and creds is None,
            unproven=saw_unproven,
        )
    )


class _CandidateHit:
    def __init__(
        self,
        *,
        verdict: str,
        response_time_ms: int | None = None,
        live_name: str | None = None,
        mac: str | None = None,
    ):
        self.verdict = verdict
        self.response_time_ms = response_time_ms
        self.live_name = live_name
        self.mac = mac


def _candidate_ips(device: Device) -> list[str]:
    """Последний IP, затем прежние адреса и DNS-имя — только если есть серийник."""
    from app.services.net_utils import NetworkInputError, assert_public_ipv4, resolve_to_ipv4

    ordered: list[str] = []

    def add(raw: str | None) -> None:
        if not raw or len(ordered) >= 8:
            return
        try:
            ip = assert_public_ipv4(raw)
        except NetworkInputError:
            return
        if ip not in ordered:
            ordered.append(ip)

    add(device.ip)
    if not discovery_service.normalize_serial(device.serial_number):
        return ordered

    rows = sorted(
        list(device.addresses or []),
        key=lambda row: row.last_seen.timestamp() if row.last_seen is not None else 0,
        reverse=True,
    )
    for row in rows:
        add(row.ip)

    for name in _dns_names(device.hostname):
        try:
            add(resolve_to_ipv4(name, timeout_s=2))
        except NetworkInputError:
            continue
    return ordered


def _dns_names(hostname: str | None) -> list[str]:
    """Короткое имя и, если домен в LDAP_DOMAIN — ещё FQDN."""
    name = (hostname or "").strip().rstrip(".")
    if not name:
        return []
    names = [name]
    if "." in name:
        return names
    domain = ""
    try:
        from flask import current_app

        domain = str(current_app.config.get("LDAP_DOMAIN") or "").strip().rstrip(".")
    except RuntimeError:
        domain = ""
    if "." in domain:
        names.append(f"{name}.{domain}")
    return names


def _probe_candidate(device: Device, ip: str, *, creds, timeout_s: int) -> _CandidateHit:
    from app.services.ping_service import ping_host

    ping = ping_host(ip, timeout_s=timeout_s)
    if ping.status != DeviceStatus.ONLINE:
        return _CandidateHit(verdict="offline")
    if not discovery_service.normalize_serial(device.serial_number):
        return _CandidateHit(verdict="no_serial", response_time_ms=ping.response_time_ms)
    live_serial = None
    live_name = None
    mac = None
    if creds is not None:
        inventory = discovery_service.lookup_wmi_inventory(ip, creds=creds)
        live_serial = inventory.serial_number
        live_name = inventory.hostname
        mac = inventory.mac
    verdict = machine_matches_card(device.serial_number, live_serial)
    return _CandidateHit(
        verdict=verdict,
        response_time_ms=ping.response_time_ms,
        live_name=live_name,
        mac=mac,
    )


def _move_primary_ip(device: Device, ip: str, mac: str | None) -> None:
    """Перенести карточку на адрес, где ответил её серийник."""
    old = (device.ip or "").strip()
    if old and old != ip:
        for row in list(device.addresses or []):
            if row.ip == old:
                row.last_status = DeviceStatus.OFFLINE
    device.ip = ip
    upsert_device_address(device, ip=ip, mac=mac, status=DeviceStatus.ONLINE)
    from app.services.ping_service import _displace_other_ip_holders

    _displace_other_ip_holders(device, ip)


def _not_found_text(
    device: Device,
    tried: list[str],
    *,
    stranger: tuple[str, str | None] | None,
    creds_missing: bool,
    unproven: bool,
) -> str:
    last = tried[0] if tried else (device.ip or "")
    if creds_missing:
        return (
            f"Адрес {last} проверен, но серийник прочитать нечем: "
            "не задана учётка WMI (Настройки → Опрос сети) и нет учётки запуска. "
            "Команда не выполнена."
        )
    if unproven and stranger is None:
        return (
            f"Адрес {last} отвечает, но серийник компьютера прочитать не удалось. "
            "Команда не выполнена."
        )
    if stranger is not None:
        ip, live_name = stranger
        who = f" ({live_name})" if live_name else ""
        return (
            f"На {ip} отвечает другой компьютер{who}. "
            "Этот серийник на прежних адресах и по DNS-имени не найден, "
            "команда не выполнена."
        )
    name = (device.hostname or "").strip()
    where = f" и по имени {name}" if name else ""
    return (
        f"{last} не отвечает. Этот компьютер{where} на других известных адресах "
        "сейчас не находится. Команда не выполнена."
    )


def _wmi_creds_for_target(user_id: int | None):
    """Учётка опроса, иначе учётка того, кто запускает команду."""
    creds = discovery_service.discovery_credentials()
    if creds is not None:
        return creds
    if user_id is None:
        return None
    from app.services.credential_service import (
        CredentialsNotConfigured,
        get_remote_admin_credentials,
    )

    try:
        remote = get_remote_admin_credentials(user_id)
    except CredentialsNotConfigured:
        return None
    return discovery_service.DiscoveryCredentials(
        username=remote.username,
        password=remote.password,
        domain=remote.domain or "",
    )


def _remember_endpoint(device: Device, status: str, response_time_ms: int | None) -> None:
    """Статус этой карточки на её текущем IP. Чужие карточки не трогаем."""
    now = utcnow()
    device.last_status = status
    device.last_response_time_ms = response_time_ms if status == DeviceStatus.ONLINE else None
    if status == DeviceStatus.ONLINE:
        device.last_seen = now
    for row in list(device.addresses or []):
        if row.ip == device.ip:
            row.last_status = status
            if status == DeviceStatus.ONLINE:
                row.last_seen = now
    db.session.add(
        DeviceHistory(
            device_id=device.id,
            timestamp=now,
            status=status,
            response_time_ms=response_time_ms if status == DeviceStatus.ONLINE else None,
        )
    )
