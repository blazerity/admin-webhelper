"""Обновить hostname в БД с самой машины (WMI), не из PTR.

Карта долго показывала reverse DNS. Несколько адресов могут резолвиться
в одно PTR-имя (устаревшая зона, DHCP не подчистил запись) — на карте
это выглядит как дубликаты. Имя Windows (Win32_ComputerSystem) другое.

Этот проход:
* берёт уже известные строки devices (не сканирует CIDR заново);
* пингует IP, у онлайн читает WMI DNSHostName/Name;
* пишет hostname, только если короткое имя ОС не совпадает с тем, что в БД;
* не сливает и не удаляет строки: разные serial — разные машины;
* если WMI-серийник не совпадает с карточкой — hostname не пишем
  (тот же IP мог отойти другому ПК в VPN-секторе).

Следующий обычный опрос больше не затирает OS-имя PTR-ом
(см. ping_service: hostname_from_wmi).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import select

from app.extensions import db
from app.models import Device, DeviceStatus
from app.services import discovery_service
from app.services.ping_service import ping_host

logger = logging.getLogger(__name__)

# WMI тяжелее ICMP; меньше параллелизма, чем у полного опроса.
_SWEEP_WORKERS = 16
_MAX_STORED_CHANGES = 200


class HostnameSweepError(RuntimeError):
    """Проход нельзя начать (нет учётки WMI)."""


@dataclass(frozen=True)
class HostnameChange:
    device_id: int
    ip: str
    serial_number: str | None
    old_hostname: str | None
    new_hostname: str
    ptr: str | None


@dataclass
class HostnameSweepResult:
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    offline: int = 0
    no_wmi: int = 0
    skipped: int = 0
    errors: int = 0
    dry_run: bool = False
    changes: list[HostnameChange] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "offline": self.offline,
            "no_wmi": self.no_wmi,
            "skipped": self.skipped,
            "errors": self.errors,
            "dry_run": self.dry_run,
            "changes": len(self.changes),
        }


@dataclass(frozen=True)
class _Target:
    device_id: int
    ip: str
    hostname: str | None
    serial_number: str | None


@dataclass
class _Probe:
    device_id: int
    ip: str
    online: bool
    os_hostname: str | None = None
    serial_number: str | None = None
    ptr: str | None = None
    error: str | None = None


def decide_hostname_update(
    current: str | None, os_hostname: str | None
) -> str | None:
    """Новое значение для devices.hostname или None — не трогать строку.

    Меняем только когда короткое имя ОС другое. FQDN с тем же коротким
    именем (n603.stepcon.ru vs n603) оставляем: это не чужой PTR.
    """
    if not os_hostname:
        return None
    if not current:
        return os_hostname
    if discovery_service.hostnames_match(current, os_hostname):
        return None
    return os_hostname


def _hostname_probe_matches_device(
    stored_serial: str | None, live_serial: str | None
) -> bool:
    """Не писать чужое OS-имя на карточку, если на IP другой SN."""
    stored = discovery_service.normalize_serial(stored_serial)
    live = discovery_service.normalize_serial(live_serial)
    if stored and live:
        return stored == live
    if stored and not live:
        return False
    return True


def run_hostname_sweep(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    device_ids: list[int] | tuple[int, ...] | None = None,
) -> HostnameSweepResult:
    """Пройти известные устройства и записать WMI-имя в hostname.

    Вызывать из потока с app.app_context() (flask CLI / admin).
    """
    creds = discovery_service.discovery_credentials()
    if creds is None:
        raise HostnameSweepError(
            "Учётка WMI не задана: имена с машин не прочитать "
            "(Настройки → Опрос сети или DISCOVERY_* в .env)."
        )

    targets = _load_targets(limit=limit, device_ids=device_ids)
    result = HostnameSweepResult(dry_run=dry_run)
    if not targets:
        return result

    probes = _probe_targets(targets, creds)
    by_id = {item.device_id: item for item in probes}
    pending: list[tuple[int, str, HostnameChange]] = []

    for target in targets:
        result.scanned += 1
        probe = by_id.get(target.device_id)
        if probe is None:
            result.errors += 1
            continue
        if probe.error:
            result.errors += 1
            continue
        if not probe.online:
            result.offline += 1
            continue
        if not _hostname_probe_matches_device(
            target.serial_number, probe.serial_number
        ):
            result.skipped += 1
            logger.info(
                "Обновление имён: %s отвечает другой машине (serial=%s), "
                "hostname id=%s не трогаем",
                target.ip,
                probe.serial_number or "—",
                target.device_id,
            )
            continue
        new_name = decide_hostname_update(target.hostname, probe.os_hostname)
        if new_name is None:
            if probe.os_hostname:
                result.unchanged += 1
            else:
                result.no_wmi += 1
            continue
        change = HostnameChange(
            device_id=target.device_id,
            ip=target.ip,
            serial_number=target.serial_number,
            old_hostname=target.hostname,
            new_hostname=new_name,
            ptr=probe.ptr,
        )
        pending.append((target.device_id, new_name, change))

    if dry_run:
        result.updated = len(pending)
        result.changes = pending_changes(pending)
        return result

    if not pending:
        return result

    try:
        for device_id, new_name, change in pending:
            device = db.session.get(Device, device_id)
            if device is None:
                result.skipped += 1
                continue
            # Повторная проверка: опрос мог успеть записать то же OS-имя.
            if decide_hostname_update(device.hostname, new_name) is None:
                result.unchanged += 1
                continue
            device.hostname = new_name
            if len(result.changes) < _MAX_STORED_CHANGES:
                result.changes.append(change)
            result.updated += 1
        db.session.commit()
    except Exception:
        logger.exception("Не удалось сохранить имена устройств")
        db.session.rollback()
        result.errors += len(pending)
        result.updated = 0
        result.changes = []
        return result

    logger.info(
        "Обновление имён: проверено %s, изменено %s, без изменений %s, "
        "офлайн %s, без WMI %s, ошибок %s",
        result.scanned,
        result.updated,
        result.unchanged,
        result.offline,
        result.no_wmi,
        result.errors,
    )
    return result


def pending_changes(
    pending: list[tuple[int, str, HostnameChange]],
) -> list[HostnameChange]:
    return [item[2] for item in pending[:_MAX_STORED_CHANGES]]


def format_change_lines(result: HostnameSweepResult) -> list[str]:
    """Строки для CLI: id, IP, serial, старое → новое имя."""
    lines = []
    for item in result.changes:
        lines.append(
            f"id={item.device_id} ip={item.ip} "
            f"serial={item.serial_number or '—'} "
            f"{item.old_hostname or '—'} → {item.new_hostname}"
            f"{'  ptr=' + item.ptr if item.ptr else ''}"
        )
    omitted = result.updated - len(result.changes)
    if omitted > 0:
        lines.append(f"… и ещё {omitted}")
    return lines


def _load_targets(
    *,
    limit: int | None,
    device_ids: list[int] | tuple[int, ...] | None,
) -> list[_Target]:
    stmt = select(Device).order_by(Device.id)
    if device_ids:
        stmt = stmt.where(Device.id.in_(list(device_ids)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    rows = list(db.session.scalars(stmt))
    targets: list[_Target] = []
    for row in rows:
        ip = (row.ip or "").strip()
        if not ip:
            continue
        targets.append(
            _Target(
                device_id=row.id,
                ip=ip,
                hostname=row.hostname,
                serial_number=row.serial_number,
            )
        )
    return targets


def _probe_targets(
    targets: list[_Target],
    creds: discovery_service.DiscoveryCredentials,
) -> list[_Probe]:
    probes: list[_Probe] = []
    with ThreadPoolExecutor(max_workers=_SWEEP_WORKERS) as pool:
        futures = [
            pool.submit(_probe_one, target, creds) for target in targets
        ]
        for future in futures:
            try:
                probes.append(future.result())
            except Exception:
                logger.exception("Рабочий поток обновления имён завершился с ошибкой")
    return probes


def _probe_one(
    target: _Target,
    creds: discovery_service.DiscoveryCredentials,
) -> _Probe:
    """Только сеть. Сессию SQLAlchemy сюда не передаём."""
    try:
        ping = ping_host(target.ip)
        ptr = discovery_service.lookup_hostname(target.ip)
        if ping.status != DeviceStatus.ONLINE:
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=False,
                ptr=ptr,
            )
        inventory = discovery_service.lookup_wmi_inventory(target.ip, creds=creds)
        return _Probe(
            device_id=target.device_id,
            ip=target.ip,
            online=True,
            os_hostname=inventory.hostname,
            serial_number=inventory.serial_number,
            ptr=ptr,
        )
    except Exception as exc:
        logger.debug("hostname sweep: %s — %s", target.ip, exc)
        return _Probe(
            device_id=target.device_id,
            ip=target.ip,
            online=False,
            error=str(exc)[:300],
        )
