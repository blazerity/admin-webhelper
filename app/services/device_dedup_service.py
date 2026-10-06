"""Поиск и удаление дублей устройств в ``devices``.

Опрос не сливает автоматически «призраков» без серийника на старом IP
и не считает одинаковый hostname ключом идентичности. Здесь — ручная
утилита: находит группы, предлагает кого оставить, удаляет выбранные.

Уверенные (confirmed):
* один и тот же IP у двух и более карточек;
* один и тот же нормализованный MAC у двух и более карточек;
* один и тот же serial_number (на случай, если unique ещё не в БД).

Предположительные (suspected):
* одинаковое hostname (без учёта регистра), если группа ещё не
  попала в confirmed по IP/MAC/SN.

В каждой группе рекомендуем оставить карточку с лучшим «весом»:
серийник, онлайн, свежий last_seen, железо, hostname/MAC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Device
from app.models.device import DeviceStatus
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


@dataclass(frozen=True)
class DuplicateGroup:
    key: str
    confidence: str  # confirmed | suspected
    reason: str
    reason_label: str
    match_value: str
    devices: tuple[DeviceDupRow, ...]
    keep_id: int


@dataclass(frozen=True)
class DedupScanResult:
    confirmed: tuple[DuplicateGroup, ...] = ()
    suspected: tuple[DuplicateGroup, ...] = ()
    scanned: int = 0

    @property
    def group_count(self) -> int:
        return len(self.confirmed) + len(self.suspected)

    @property
    def device_count(self) -> int:
        seen: set[int] = set()
        for group in (*self.confirmed, *self.suspected):
            for row in group.devices:
                seen.add(row.id)
        return len(seen)


@dataclass
class DedupDeleteResult:
    deleted: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)


def find_duplicate_groups() -> DedupScanResult:
    """Просканировать все устройства и вернуть группы дублей."""
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
    confirmed_sets: list[tuple[str, str, str, set[int]]] = []
    # reason_code, reason_label, match_value, ids

    _collect_key_groups(
        devices,
        key_fn=_serial_key,
        reason="serial",
        reason_label="одинаковый серийный номер",
        into=confirmed_sets,
    )
    _collect_key_groups(
        devices,
        key_fn=_ip_key,
        reason="ip",
        reason_label="один и тот же IP",
        into=confirmed_sets,
    )
    _collect_key_groups(
        devices,
        key_fn=_mac_key,
        reason="mac",
        reason_label="одинаковый MAC",
        into=confirmed_sets,
    )

    confirmed_ids: set[int] = set()
    for _, _, _, ids in confirmed_sets:
        confirmed_ids.update(ids)

    suspected_sets: list[tuple[str, str, str, set[int]]] = []
    _collect_key_groups(
        devices,
        key_fn=_hostname_key,
        reason="hostname",
        reason_label="одинаковое имя хоста",
        into=suspected_sets,
        skip_ids=confirmed_ids,
        min_size=2,
    )

    confirmed = tuple(
        _build_group(by_id, reason, reason_label, match_value, ids, "confirmed")
        for reason, reason_label, match_value, ids in _merge_overlapping(confirmed_sets)
    )
    suspected = tuple(
        _build_group(by_id, reason, reason_label, match_value, ids, "suspected")
        for reason, reason_label, match_value, ids in suspected_sets
        if len(ids) >= 2
    )

    return DedupScanResult(
        confirmed=confirmed,
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


def _serial_key(device: Device) -> str | None:
    value = (device.serial_number or "").strip().upper()
    return value or None


def _ip_key(device: Device) -> str | None:
    value = (device.ip or "").strip()
    return value or None


def _mac_key(device: Device) -> str | None:
    return normalize_mac(device.mac)


def _hostname_key(device: Device) -> str | None:
    value = (device.hostname or "").strip().lower().rstrip(".")
    return value or None


def _collect_key_groups(
    devices: list[Device],
    *,
    key_fn,
    reason: str,
    reason_label: str,
    into: list[tuple[str, str, str, set[int]]],
    skip_ids: set[int] | None = None,
    min_size: int = 2,
) -> None:
    buckets: dict[str, set[int]] = {}
    for device in devices:
        if skip_ids and device.id in skip_ids:
            continue
        key = key_fn(device)
        if not key:
            continue
        buckets.setdefault(key, set()).add(device.id)
    for match_value, ids in sorted(buckets.items(), key=lambda item: item[0]):
        if len(ids) < min_size:
            continue
        # Для hostname: если все id уже в skip — не попадём сюда;
        # если часть в skip, всё равно не собираем (передаём skip раньше).
        into.append((reason, reason_label, match_value, ids))


def _merge_overlapping(
    groups: list[tuple[str, str, str, set[int]]],
) -> list[tuple[str, str, str, set[int]]]:
    """Склеить пересекающиеся confirmed-группы в одну с объединённым описанием."""
    if not groups:
        return []

    merged: list[tuple[str, str, str, set[int]]] = []
    for reason, reason_label, match_value, ids in groups:
        hit_indexes = [
            index
            for index, (_, _, _, existing) in enumerate(merged)
            if existing & ids
        ]
        if not hit_indexes:
            merged.append((reason, reason_label, match_value, set(ids)))
            continue
        anchor = hit_indexes[0]
        a_reason, a_label, a_match, a_ids = merged[anchor]
        a_ids |= ids
        labels = [a_label]
        matches = [a_match]
        reasons = [a_reason]
        if reason_label not in labels:
            labels.append(reason_label)
            matches.append(match_value)
            reasons.append(reason)
        for index in reversed(hit_indexes[1:]):
            o_reason, o_label, o_match, o_ids = merged.pop(index)
            a_ids |= o_ids
            if o_label not in labels:
                labels.append(o_label)
                matches.append(o_match)
                reasons.append(o_reason)
        merged[anchor] = (
            "+".join(reasons),
            "; ".join(labels),
            " / ".join(matches),
            a_ids,
        )
    return merged


def _build_group(
    by_id: dict[int, Device],
    reason: str,
    reason_label: str,
    match_value: str,
    ids: set[int],
    confidence: str,
) -> DuplicateGroup:
    devices = [by_id[device_id] for device_id in sorted(ids) if device_id in by_id]
    scored = [(device, _keep_score(device)) for device in devices]
    scored.sort(key=lambda item: _keep_sort_key(item[0], item[1]))
    keep_id = scored[0][0].id if scored else 0
    rows = tuple(
        _to_row(device, score, keep_recommended=(device.id == keep_id))
        for device, score in scored
    )
    key = f"{confidence}:{reason}:{match_value}"
    return DuplicateGroup(
        key=key,
        confidence=confidence,
        reason=reason,
        reason_label=reason_label,
        match_value=match_value,
        devices=rows,
        keep_id=keep_id,
    )


def _keep_score(device: Device) -> int:
    score = 0
    if (device.serial_number or "").strip():
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
    # Выше score → оставить; при равенстве — более свежий last_seen, затем больший id.
    ts = last_seen.timestamp() if last_seen is not None else 0.0
    return (-score, -ts, -int(device.id or 0))


def _to_row(device: Device, score: int, *, keep_recommended: bool) -> DeviceDupRow:
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
    )
