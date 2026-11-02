"""Снимок железа из карточки bAD (ответ POST /v1/integrations/lookup).

Поля те же, что раньше писал WMI: процессор, ОЗУ, диски, версия ОС.
bad_id берём из ответа как есть и не вычисляем.
device_id (MachineGuid) в снимок не кладём.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.bad_integration import is_bad_id
from app.services.hardware_info import HardwareSnapshot, build_hardware_snapshot

_REMOVABLE_IFACES = {"USB", "1394"}


@dataclass(frozen=True)
class BadHardwareCard:
    bad_id: str | None
    snapshot: HardwareSnapshot
    serial_number: str | None
    hostname: str | None


def card_from_lookup(payload: dict | None) -> BadHardwareCard:
    """Разобрать карточку устройства. Неизвестный вид — пустой снимок."""
    data = payload if isinstance(payload, dict) else {}
    bad_id = str(data.get("bad_id") or "").strip()
    if not is_bad_id(bad_id):
        bad_id = ""

    inventory = data.get("latest_inventory")
    body: dict = {}
    if isinstance(inventory, dict) and isinstance(inventory.get("payload"), dict):
        body = inventory["payload"]
    hardware = body.get("hardware") if isinstance(body.get("hardware"), dict) else {}
    host = body.get("host") if isinstance(body.get("host"), dict) else {}
    os_info = host.get("os") if isinstance(host.get("os"), dict) else {}

    serial = _text(hardware.get("serial_number")) or _text(data.get("serial_number"))
    hostname = _text(host.get("hostname")) or _text(data.get("hostname"))
    snapshot = build_hardware_snapshot(
        cpu_name=_cpu_name(hardware.get("cpu")),
        ram_bytes=_int(hardware.get("ram_bytes")),
        disk_bytes=_disk_bytes(hardware),
        os_caption=_text(os_info.get("caption")),
        os_version=_text(os_info.get("version")),
        os_build=_text(os_info.get("build")),
        os_display_version=_text(os_info.get("display_version")),
        os_edition_id=_text(os_info.get("edition_id")),
    )
    return BadHardwareCard(
        bad_id=bad_id or None,
        snapshot=snapshot,
        serial_number=serial,
        hostname=hostname,
    )


def _cpu_name(value) -> str | None:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return _text(value.get("name"))
    return None


def _disk_bytes(hardware: dict) -> int | None:
    disks = hardware.get("disks")
    total = _sum_sizes(disks, skip_removable=True)
    if total is not None:
        return total
    return _sum_sizes(hardware.get("volumes"), skip_removable=False)


def _sum_sizes(items, *, skip_removable: bool) -> int | None:
    if not isinstance(items, list):
        return None
    total = 0
    found = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if skip_removable and _is_removable(item):
            continue
        size = _int(item.get("size_bytes"))
        if size is None or size <= 0:
            continue
        total += size
        found = True
    return total if found else None


def _is_removable(item: dict) -> bool:
    if item.get("removable") is True:
        return True
    iface = str(item.get("interface_type") or "").strip().upper()
    if iface in _REMOVABLE_IFACES:
        return True
    media = str(item.get("media_type") or "").lower()
    return "removable" in media


def _text(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
