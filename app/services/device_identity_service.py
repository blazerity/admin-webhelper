"""Идентичность устройств: адреса интерфейсов и слияние «призраков».

Паспорт машины — только ``serial_number``. IP/MAC — атрибуты сетевых
интерфейсов (их может быть несколько: Ethernet + Wi‑Fi). Призрак без SN
на старом IP после появления SN на новом — сливаем в карточку с SN,
если совпал IP или WMI-hostname.
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
