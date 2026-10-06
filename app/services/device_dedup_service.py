"""Поиск и удаление дублей устройств в ``devices``.

Идентичность машины — только serial_number. IP и MAC — сетевые атрибуты
(DHCP и несколько интерфейсов), сами по себе не делают карточки «одной
машиной».

Секции:
* confirmed — две+ карточки с одинаковым SN (настоящий дубль БД);
* ghosts — один IP: есть карточка с SN и карточка без SN (призрак);
* ip_history — один IP, у всех разные непустые SN (переиспользование адреса,
  не дубли; галочки по умолчанию сняты);
* suspected — одинаковый MAC при пустых SN или одинаковый hostname
  (слабый сигнал, без авто-галочек).

В confirmed/ghosts рекомендуем кого оставить; лишние отмечены на удаление.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Device
from app.models.device import DeviceStatus
from app.services import discovery_service
from app.services.net_utils import normalize_mac
from app.utils import as_utc


class DeviceDedupError(ValueError):
    """Ошибка утилиты дублей. Текст можно показать во flash."""


@dataclass(frozen=True)
class DeviceDupRow:
    id: int
    ip: str
    hostname: str | None
    serial_number: str | None
    mac: str | None
    sector_id: int
    sector_name: str
    last_status: str
    last_seen: datetime | None
    kind_label: str
    has_hardware: bool
    score: int
    keep_recommended: bool
    delete_prechecked: bool


@dataclass(frozen=True)
class DuplicateGroup:
    key: str
    confidence: str  # confirmed | ghosts | ip_history | suspected
    reason: str
    reason_label: str
    match_value: str
    devices: tuple[DeviceDupRow, ...]
    keep_id: int


@dataclass(frozen=True)
class DedupScanResult:
    confirmed: tuple[DuplicateGroup, ...] = ()
    ghosts: tuple[DuplicateGroup, ...] = ()
    ip_history: tuple[DuplicateGroup, ...] = ()
    suspected: tuple[DuplicateGroup, ...] = ()
    scanned: int = 0

    @property
    def group_count(self) -> int:
        return (
            len(self.confirmed)
            + len(self.ghosts)
            + len(self.ip_history)
            + len(self.suspected)
        )

    @property
    def actionable_count(self) -> int:
        """Группы, где имеет смысл авто-отмечать удаление."""
        return len(self.confirmed) + len(self.ghosts)

    @property
    def device_count(self) -> int:
        seen: set[int] = set()
        for group in (
            *self.confirmed,
            *self.ghosts,
            *self.ip_history,
            *self.suspected,
        ):
            for row in group.devices:
                seen.add(row.id)
        return len(seen)


@dataclass
class DedupDeleteResult:
    deleted: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)


def find_duplicate_groups() -> DedupScanResult:
    """Просканировать все устройства и вернуть группы по правилам выше."""
    devices = list(
        db.session.scalars(
            select(Device)
            .options(selectinload(Device.sector))
            .order_by(Device.id)
        ).all()
    )
    if not devices:
        return DedupScanResult(scanned=0)

    by_id = {device.id: device for device in devices}

    confirmed = _groups_same_serial(by_id, devices)
    confirmed_ids = {row.id for group in confirmed for row in group.devices}

    ghosts, ip_history = _groups_by_ip(by_id, devices, skip_ids=confirmed_ids)
    occupied = confirmed_ids | {
        row.id for group in (*ghosts, *ip_history) for row in group.devices
    }

    suspected = _groups_suspected(by_id, devices, skip_ids=occupied)

    return DedupScanResult(
        confirmed=confirmed,
        ghosts=ghosts,
        ip_history=ip_history,
        suspected=suspected,
        scanned=len(devices),
    )


def delete_devices(device_ids: list[int] | tuple[int, ...]) -> DedupDeleteResult:
    """Удалить выбранные устройства. История и железо — по CASCADE."""
    raw_ids = [int(item) for item in device_ids]
    unique_ids = list(dict.fromkeys(raw_ids))
    if not unique_ids:
        raise DeviceDedupError("Не выбрано ни одного устройства для удаления.")

    result = DedupDeleteResult()
    for device_id in unique_ids:
        device = db.session.get(Device, device_id)
        if device is None:
            result.missing.append(device_id)
            continue
        db.session.delete(device)
        result.deleted.append(device_id)

    if not result.deleted:
        db.session.rollback()
        raise DeviceDedupError("Выбранные устройства уже отсутствуют в базе.")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return result


def _groups_same_serial(
    by_id: dict[int, Device],
    devices: list[Device],
) -> tuple[DuplicateGroup, ...]:
    buckets: dict[str, set[int]] = {}
    for device in devices:
        key = discovery_service.normalize_serial(device.serial_number)
        if not key:
            continue
        buckets.setdefault(key, set()).add(device.id)
    groups = []
    for match_value, ids in sorted(buckets.items()):
        if len(ids) < 2:
            continue
        groups.append(
            _build_group(
                by_id,
                reason="serial",
                reason_label="одинаковый серийный номер",
                match_value=match_value,
                ids=ids,
                confidence="confirmed",
                precheck="not_keep",
            )
        )
    return tuple(groups)


def _groups_by_ip(
    by_id: dict[int, Device],
    devices: list[Device],
    *,
    skip_ids: set[int],
) -> tuple[tuple[DuplicateGroup, ...], tuple[DuplicateGroup, ...]]:
    buckets: dict[str, list[Device]] = {}
    for device in devices:
        if device.id in skip_ids:
            continue
        ip = (device.ip or "").strip()
        if not ip:
            continue
        buckets.setdefault(ip, []).append(device)

    ghosts: list[DuplicateGroup] = []
    history: list[DuplicateGroup] = []
    for ip, rows in sorted(buckets.items()):
        if len(rows) < 2:
            continue
        with_sn = [d for d in rows if discovery_service.normalize_serial(d.serial_number)]
        without_sn = [d for d in rows if not discovery_service.normalize_serial(d.serial_number)]
        ids = {d.id for d in rows}
        serials = {
            discovery_service.normalize_serial(d.serial_number)
            for d in with_sn
        }
        serials.discard(None)

        if with_sn and without_sn:
            ghosts.append(
                _build_group(
                    by_id,
                    reason="ghost",
                    reason_label="призрак без SN на том же IP",
                    match_value=ip,
                    ids=ids,
                    confidence="ghosts",
                    precheck="no_serial",
                )
            )
        elif len(serials) >= 2 and not without_sn:
            # Разные service tag на одном IP — история DHCP, не дубль.
            history.append(
                _build_group(
                    by_id,
                    reason="ip_history",
                    reason_label="переиспользование IP (разные машины)",
                    match_value=ip,
                    ids=ids,
                    confidence="ip_history",
                    precheck="none",
                )
            )
        elif without_sn and not with_sn and len(without_sn) >= 2:
            # Несколько заглушек на одном IP без SN.
            ghosts.append(
                _build_group(
                    by_id,
                    reason="ghost_stubs",
                    reason_label="несколько записей без SN на одном IP",
                    match_value=ip,
                    ids=ids,
                    confidence="ghosts",
                    precheck="not_keep",
                )
            )
    return tuple(ghosts), tuple(history)


def _groups_suspected(
    by_id: dict[int, Device],
    devices: list[Device],
    *,
    skip_ids: set[int],
) -> tuple[DuplicateGroup, ...]:
    groups: list[DuplicateGroup] = []

    mac_buckets: dict[str, set[int]] = {}
    for device in devices:
        if device.id in skip_ids:
            continue
        mac = normalize_mac(device.mac)
        if not mac:
            continue
        mac_buckets.setdefault(mac, set()).add(device.id)
    for match_value, ids in sorted(mac_buckets.items()):
        if len(ids) < 2:
            continue
        rows = [by_id[i] for i in ids if i in by_id]
        serials = {
            discovery_service.normalize_serial(d.serial_number)
            for d in rows
        }
        serials.discard(None)
        # Разные SN + один MAC — не предлагаем как дубль (ошибка данных / редко).
        if len(serials) >= 2:
            continue
        # Один SN уже в confirmed. Здесь — только пустые SN.
        if serials:
            continue
        groups.append(
            _build_group(
                by_id,
                reason="mac",
                reason_label="одинаковый MAC без серийников",
                match_value=match_value,
                ids=ids,
                confidence="suspected",
                precheck="none",
            )
        )

    host_buckets: dict[str, set[int]] = {}
    for device in devices:
        if device.id in skip_ids:
            continue
        key = discovery_service.hostname_key(device.hostname)
        if not key:
            continue
        host_buckets.setdefault(key, set()).add(device.id)
    for match_value, ids in sorted(host_buckets.items()):
        if len(ids) < 2:
            continue
        # Уже учтены выше — не дублируем.
        if ids & skip_ids:
            continue
        rows = [by_id[i] for i in ids if i in by_id]
        serials = {
            discovery_service.normalize_serial(d.serial_number)
            for d in rows
        }
        serials.discard(None)
        if len(serials) >= 2:
            # Разные машины с одним именем — только подсказка, без галочек.
            groups.append(
                _build_group(
                    by_id,
                    reason="hostname",
                    reason_label="одинаковое имя хоста (разные SN)",
                    match_value=match_value,
                    ids=ids,
                    confidence="suspected",
                    precheck="none",
                )
            )
        elif not serials:
            groups.append(
                _build_group(
                    by_id,
                    reason="hostname",
                    reason_label="одинаковое имя хоста без серийников",
                    match_value=match_value,
                    ids=ids,
                    confidence="suspected",
                    precheck="none",
                )
            )
    return tuple(groups)


def _build_group(
    by_id: dict[int, Device],
    *,
    reason: str,
    reason_label: str,
    match_value: str,
    ids: set[int],
    confidence: str,
    precheck: str,
) -> DuplicateGroup:
    devices = [by_id[device_id] for device_id in sorted(ids) if device_id in by_id]
    scored = [(device, _keep_score(device)) for device in devices]
    scored.sort(key=lambda item: _keep_sort_key(item[0], item[1]))
    keep_id = scored[0][0].id if scored else 0
    rows = []
    for device, score in scored:
        keep = device.id == keep_id
        delete_prechecked = False
        if precheck == "not_keep":
            delete_prechecked = not keep
        elif precheck == "no_serial":
            delete_prechecked = not discovery_service.normalize_serial(device.serial_number)
        elif precheck == "none":
            delete_prechecked = False
        rows.append(
            _to_row(
                device,
                score,
                keep_recommended=keep,
                delete_prechecked=delete_prechecked,
            )
        )
    key = f"{confidence}:{reason}:{match_value}"
    return DuplicateGroup(
        key=key,
        confidence=confidence,
        reason=reason,
        reason_label=reason_label,
        match_value=match_value,
        devices=tuple(rows),
        keep_id=keep_id,
    )


def _keep_score(device: Device) -> int:
    score = 0
    if discovery_service.normalize_serial(device.serial_number):
        score += 1000
    if device.last_status == DeviceStatus.ONLINE:
        score += 200
    elif device.last_status == DeviceStatus.OFFLINE:
        score += 50
    if device.hardware_checked_at is not None:
        score += 80
    if (device.hostname or "").strip():
        score += 40
    if normalize_mac(device.mac):
        score += 40
    if device.cpu_name or device.ram_gb or device.disk_gb:
        score += 30
    return score


def _keep_sort_key(device: Device, score: int) -> tuple:
    last_seen = as_utc(device.last_seen) if device.last_seen else None
    ts = last_seen.timestamp() if last_seen is not None else 0.0
    return (-score, -ts, -int(device.id or 0))


def _to_row(
    device: Device,
    score: int,
    *,
    keep_recommended: bool,
    delete_prechecked: bool,
) -> DeviceDupRow:
    sector = device.sector
    return DeviceDupRow(
        id=device.id,
        ip=device.ip,
        hostname=device.hostname,
        serial_number=device.serial_number,
        mac=device.mac,
        sector_id=device.sector_id,
        sector_name=(sector.name if sector is not None else "—"),
        last_status=device.last_status or DeviceStatus.UNKNOWN,
        last_seen=device.last_seen,
        kind_label=device.kind_label,
        has_hardware=bool(
            device.hardware_checked_at
            or device.cpu_name
            or device.ram_gb
            or device.disk_gb
        ),
        score=score,
        keep_recommended=keep_recommended,
        delete_prechecked=delete_prechecked,
    )
