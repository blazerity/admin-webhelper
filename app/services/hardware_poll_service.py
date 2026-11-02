"""Ежедневный опрос конфигурации железа на Windows-машинах.

Отдельный проход, не ICMP-опрос сети: берём уже известные устройства,
которые похожи на Windows (серийник, имена n/w/v/серверы, fingerprint),
пингуем и спрашиваем bAD. Процессор, память, диски и версию Windows
из WMI больше не читаем — их приносит lookup.

На машине той же учёткой, что ходила в WMI, проверяем службу badagent.
Службы нет — новый снимок не пишем. Служба есть — берём device_id из
конфига агента (только это поле) либо из MachineGuid и вызываем
POST /v1/integrations/lookup. На карточку кладём bad_id из ответа.
Серийник снимка bAD остаётся паспортом: чужой ПК на том же IP снимок не получает.

Пока связь с bAD не «подключено», проход не стартует и старый WMI-опрос
железа не подменяет lookup.

Планировщик: cron из app_settings, по умолчанию полдень локального TZ.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import Device, DeviceHardwareHistory, DeviceStatus, HardwarePollRun
from app.services import discovery_service
from app.services.device_kind import KIND_DESKTOP, KIND_NOTEBOOK, KIND_SERVER, KIND_VDS
from app.services.hardware_info import HardwareSnapshot
from app.services.ping_service import ping_host
from app.utils import as_utc, utcnow

logger = logging.getLogger(__name__)

_WINDOWS_KINDS = {KIND_NOTEBOOK, KIND_DESKTOP, KIND_VDS, KIND_SERVER}
_SWEEP_WORKERS = 8
_STALE_POLL_AFTER = timedelta(hours=2)
_RECENT_RUNS_LIMIT = 20
_run_lock = threading.Lock()
_MAX_STORED_CHANGES = 200


class HardwarePollError(RuntimeError):
    """Проход нельзя начать (нет связи с bAD или учётки WMI)."""


class HardwarePollInProgressError(RuntimeError):
    """Опрос железа уже идёт."""


@dataclass(frozen=True)
class HardwareChange:
    device_id: int
    ip: str
    hostname: str | None
    os_label: str
    cpu_name: str | None
    ram_gb: int | None
    disk_gb: int | None


@dataclass
class HardwarePollResult:
    scanned: int = 0
    online: int = 0
    offline: int = 0
    collected: int = 0
    changed: int = 0
    skipped: int = 0
    mismatched: int = 0
    no_wmi: int = 0
    no_agent: int = 0
    unregistered: int = 0
    errors: int = 0
    dry_run: bool = False
    mode: str = "scheduled"
    error: str = ""
    changes: list[HardwareChange] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "online": self.online,
            "offline": self.offline,
            "collected": self.collected,
            "changed": self.changed,
            "skipped": self.skipped,
            "mismatched": self.mismatched,
            "no_wmi": self.no_wmi,
            "no_agent": self.no_agent,
            "unregistered": self.unregistered,
            "errors": self.errors,
            "dry_run": self.dry_run,
            "mode": self.mode,
            "error": self.error,
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
    snapshot: HardwareSnapshot | None = None
    serial_number: str | None = None
    hostname: str | None = None
    bad_id: str | None = None
    no_agent: bool = False
    unregistered: bool = False
    revoked: bool = False
    error: str | None = None
    # Текст для карточки. Без MachineGuid, ключей и текста исключения.
    note: str = ""


def is_windows_hardware_target(device: Device) -> bool:
    """Windows: есть WMI-серийник, своё имя n/w/v/сервер, или TCP-отпечаток windows."""
    if (device.serial_number or "").strip():
        return True
    if device.kind in _WINDOWS_KINDS:
        return True
    return (device.fingerprint_kind or "").strip().lower() == "windows"


def resolve_hardware_owner(
    target: Device,
    *,
    live_serial: str | None,
    live_hostname: str | None,
    by_serial: dict[str, Device] | None = None,
    allow_remap: bool = True,
) -> Device | None:
    """На какую карточку писать снимок. None — IP занял чужой ПК, не пишем.

    VPN/DHCP отдаёт тот же адрес другой машине. Ключ — serial_number.
    Hostname — запасной, только если зонд серийник не отдал.
    Если SN зонда принадлежит другой известной карточке и allow_remap,
    снимок уходит ей, а не карточке с устаревшим IP.
    """
    live_serial = discovery_service.normalize_serial(live_serial)
    stored = discovery_service.normalize_serial(target.serial_number)
    if live_serial:
        if stored == live_serial:
            return target
        other = (by_serial or {}).get(live_serial)
        if other is not None and other.id != target.id:
            return other if allow_remap else None
        if stored is None:
            return target
        return None
    if stored:
        if discovery_service.hostnames_match(target.hostname, live_hostname):
            return target
        return None
    return target


def apply_hardware_snapshot(
    device: Device,
    snapshot: HardwareSnapshot,
    *,
    collected_at=None,
    record_history: bool = True,
) -> bool:
    """Записать текущий снимок. История — только если железо/ОС изменились.

    Возвращает True, если identity сменился (или это первый снимок).
    """
    when = collected_at or utcnow()
    changed = device.hardware_identity != snapshot.identity
    first = device.hardware_checked_at is None
    device.cpu_name = snapshot.cpu_name
    device.ram_gb = snapshot.ram_gb
    device.disk_gb = snapshot.disk_gb
    device.os_caption = snapshot.os_caption
    device.os_family = snapshot.os_family
    device.os_edition = snapshot.os_edition
    device.os_display_version = snapshot.os_display_version
    device.os_build = snapshot.os_build
    device.hardware_checked_at = when
    if record_history and (changed or first):
        db.session.add(
            DeviceHardwareHistory(
                device_id=device.id,
                collected_at=when,
                cpu_name=snapshot.cpu_name,
                ram_gb=snapshot.ram_gb,
                disk_gb=snapshot.disk_gb,
                os_caption=snapshot.os_caption,
                os_family=snapshot.os_family,
                os_edition=snapshot.os_edition,
                os_display_version=snapshot.os_display_version,
                os_build=snapshot.os_build,
            )
        )
    return changed or first


def count_hardware_poll_runs() -> int:
    """Число записей в hardware_poll_runs."""
    return int(db.session.scalar(select(func.count()).select_from(HardwarePollRun)) or 0)


def load_recent_hardware_poll_runs(
    limit: int = _RECENT_RUNS_LIMIT,
    *,
    offset: int = 0,
) -> list[HardwarePollRun]:
    """Последние прогоны опроса железа, новые сверху.

    offset — для пагинации на странице настроек (CSV по-прежнему берёт limit).
    """
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset or 0))
    return list(
        db.session.scalars(
            select(HardwarePollRun)
            .order_by(HardwarePollRun.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


def load_device_hardware_history(
    device_id: int, *, limit: int = 20, offset: int = 0
) -> list[DeviceHardwareHistory]:
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset or 0))
    return list(
        db.session.scalars(
            select(DeviceHardwareHistory)
            .where(DeviceHardwareHistory.device_id == device_id)
            .order_by(DeviceHardwareHistory.collected_at.desc(), DeviceHardwareHistory.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


def count_device_hardware_history(device_id: int) -> int:
    return int(
        db.session.scalar(
            select(func.count())
            .select_from(DeviceHardwareHistory)
            .where(DeviceHardwareHistory.device_id == device_id)
        )
        or 0
    )


def run_hardware_poll(
    *,
    mode: str = "scheduled",
    dry_run: bool = False,
    limit: int | None = None,
    device_ids: list[int] | tuple[int, ...] | None = None,
) -> HardwarePollResult:
    """Пройти Windows-устройства и записать снимок железа.

    Вызывать из потока с app.app_context() (планировщик / flask CLI / admin).
    """
    if not _run_lock.acquire(blocking=False):
        raise HardwarePollInProgressError("Опрос железа уже выполняется")
    started = utcnow()
    run: HardwarePollRun | None = None
    result = HardwarePollResult(dry_run=dry_run, mode=mode or "scheduled")
    try:
        from app.services.bad_integration import connected_credentials

        bad_credentials = connected_credentials()
        if bad_credentials is None:
            raise HardwarePollError(
                "Интеграция с bAD не подключена. Железо берём только из lookup, "
                "когда связь в состоянии «подключено» (Настройки → Опросы железа)."
            )
        creds = discovery_service.discovery_credentials()
        if creds is None:
            raise HardwarePollError(
                "Учётка WMI не задана: службу badagent не проверить "
                "(Настройки → Опрос сети или DISCOVERY_* в .env)."
            )

        if not dry_run:
            _close_stale_runs(started)
            active = _active_run(started)
            if active is not None:
                raise HardwarePollInProgressError(
                    "Опрос железа уже выполняется (другой процесс)."
                )
            run = HardwarePollRun(started_at=started, mode=result.mode)
            db.session.add(run)
            db.session.commit()

        try:
            _poll_targets(
                result,
                creds,
                bad_credentials=bad_credentials,
                limit=limit,
                device_ids=device_ids,
            )
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)[:2000]
            result.errors += 1
            if run is not None:
                run_id = run.id
                # Не коммитим частичные snapshot'ы устройств — только статус прогона.
                db.session.rollback()
                failed = db.session.get(HardwarePollRun, run_id)
                if failed is not None:
                    failed.finished_at = utcnow()
                    failed.error = result.error
                    failed.scanned = result.scanned
                    failed.errors = result.errors
                    db.session.commit()
            raise

        if run is not None:
            run.finished_at = utcnow()
            run.scanned = result.scanned
            run.online = result.online
            run.offline = result.offline
            run.collected = result.collected
            run.changed = result.changed
            run.skipped = result.skipped
            run.errors = result.errors
            db.session.commit()

        logger.info(
            "Опрос железа: проверено %s, онлайн %s, собрано %s, изменено %s, "
            "офлайн %s, без агента %s, не в bAD %s, чужой IP %s, ошибок %s",
            result.scanned,
            result.online,
            result.collected,
            result.changed,
            result.offline,
            result.no_agent,
            result.unregistered,
            result.mismatched,
            result.errors,
        )
        return result
    finally:
        _run_lock.release()


def poll_one_device(device: Device) -> HardwarePollResult:
    """Опросить одно устройство (кнопка на карточке)."""
    return run_hardware_poll(mode="manual", device_ids=(device.id,))


def format_change_lines(result: HardwarePollResult) -> list[str]:
    lines = []
    for item in result.changes:
        lines.append(
            f"id={item.device_id} ip={item.ip} {item.hostname or '—'} "
            f"cpu={item.cpu_name or '—'} ram={_gb(item.ram_gb)} "
            f"disk={_gb(item.disk_gb)} os={item.os_label or '—'}"
        )
    omitted = result.changed - len(result.changes)
    if omitted > 0:
        lines.append(f"… и ещё {omitted}")
    return lines


def _gb(value: int | None) -> str:
    return f"{value} ГБ" if value is not None else "—"


def _poll_targets(
    result: HardwarePollResult,
    creds: discovery_service.DiscoveryCredentials,
    *,
    bad_credentials: tuple[str, str],
    limit: int | None,
    device_ids: list[int] | tuple[int, ...] | None,
) -> None:
    targets = _load_targets(limit=limit, device_ids=device_ids)
    if not targets:
        return
    probes = _probe_targets(targets, creds, bad_credentials)
    if any(item.revoked for item in probes):
        from app.services.bad_integration import mark_revoked

        mark_revoked()
    by_id = {item.device_id: item for item in probes}
    now = utcnow()
    allow_remap = not device_ids

    pending: list[tuple[_Target, HardwareSnapshot | None, str | None, str | None, str | None]] = []
    unregistered_ids: list[int] = []
    notes: dict[int, str] = {}
    for target in targets:
        result.scanned += 1
        probe = by_id.get(target.device_id)
        if probe is None:
            result.errors += 1
            notes[target.device_id] = "Опрос этой машины не завершился."
            continue
        if probe.note:
            notes[target.device_id] = probe.note
        if probe.error:
            result.errors += 1
            continue
        if not probe.online:
            result.offline += 1
            continue
        result.online += 1
        if probe.no_agent:
            result.no_agent += 1
            continue
        if probe.unregistered:
            result.unregistered += 1
            unregistered_ids.append(target.device_id)
            continue
        snapshot = probe.snapshot
        if (snapshot is None or snapshot.is_empty) and not probe.bad_id:
            result.no_wmi += 1
            continue
        pending.append(
            (target, snapshot, probe.serial_number, probe.hostname, probe.bad_id)
        )

    if unregistered_ids and not result.dry_run:
        _mark_unregistered(unregistered_ids)

    if not pending:
        if not result.dry_run and (unregistered_ids or notes):
            _save_poll_notes(notes, now)
            db.session.commit()
        return

    from app.services.device_identity_service import absorb_ghosts, upsert_device_address

    target_ids = [item[0].device_id for item in pending]
    live_serials = {
        discovery_service.normalize_serial(serial)
        for _, _, serial, _, _ in pending
        if discovery_service.normalize_serial(serial)
    }
    devices = {
        device.id: device
        for device in db.session.scalars(select(Device).where(Device.id.in_(target_ids)))
    }
    by_serial: dict[str, Device] = {}
    if live_serials:
        by_serial = {
            device.serial_number: device
            for device in db.session.scalars(
                select(Device).where(Device.serial_number.in_(list(live_serials)))
            )
            if device.serial_number
        }

    for target, snapshot, live_serial, live_hostname, bad_id in pending:
        probed = devices.get(target.device_id)
        if probed is None:
            result.skipped += 1
            continue
        owner = resolve_hardware_owner(
            probed,
            live_serial=live_serial,
            live_hostname=live_hostname,
            by_serial=by_serial,
            allow_remap=allow_remap,
        )
        if owner is None:
            result.skipped += 1
            result.mismatched += 1
            notes[probed.id] = (
                "Lookup bAD ответил, но серийник снимка не совпал с этой карточкой. "
                "Снимок не записан."
            )
            logger.info(
                "Опрос железа: %s отвечает другой машине (serial=%s hostname=%s), "
                "снимок не пишем на id=%s serial=%s",
                target.ip,
                live_serial or "—",
                live_hostname or "—",
                probed.id,
                probed.serial_number or "—",
            )
            continue
        if owner.id != probed.id:
            result.mismatched += 1
            devices[owner.id] = owner
            notes[probed.id] = (
                "Серийник снимка принадлежит другой карточке. Снимок записан туда."
            )
        live_serial_norm = discovery_service.normalize_serial(live_serial)
        change = HardwareChange(
            device_id=owner.id,
            ip=target.ip,
            hostname=owner.hostname or live_hostname or target.hostname,
            os_label=snapshot.os_label if snapshot is not None else "",
            cpu_name=snapshot.cpu_name if snapshot is not None else None,
            ram_gb=snapshot.ram_gb if snapshot is not None else None,
            disk_gb=snapshot.disk_gb if snapshot is not None else None,
        )
        if result.dry_run:
            result.collected += 1
            hardware_changed = (
                snapshot is not None
                and not snapshot.is_empty
                and (
                    owner.hardware_identity != snapshot.identity
                    or owner.hardware_checked_at is None
                )
            )
            if hardware_changed or (bad_id and owner.bad_id != bad_id):
                result.changed += 1
                if len(result.changes) < _MAX_STORED_CHANGES and snapshot is not None:
                    result.changes.append(change)
            continue

        if live_serial_norm and not discovery_service.normalize_serial(owner.serial_number):
            owner.serial_number = live_serial_norm
            by_serial[live_serial_norm] = owner
        if live_hostname and not owner.hostname:
            owner.hostname = live_hostname

        upsert_device_address(
            owner,
            ip=target.ip,
            mac=None,
            status=DeviceStatus.ONLINE,
        )
        owner.ip = target.ip
        owner.last_status = DeviceStatus.ONLINE
        owner.last_seen = now
        if live_serial_norm:
            absorb_ghosts(
                owner,
                ip=target.ip,
                hostname=live_hostname,
                hostname_trusted=bool(live_hostname),
            )
        bad_changed = bool(bad_id) and owner.bad_id != bad_id
        if bad_id:
            _assign_bad_id(owner, bad_id)
        if bad_id:
            notes[owner.id] = (
                f"Lookup bAD ответил. На карточку записан {bad_id}. "
                "device_id с машины в базу не сохранён."
            )
        else:
            notes[owner.id] = (
                "Lookup bAD ответил, снимок записан. bad_id в ответе не было. "
                "device_id в базу не сохраняется."
            )
        changed = bad_changed
        if snapshot is not None and not snapshot.is_empty:
            changed = apply_hardware_snapshot(owner, snapshot, collected_at=now) or changed
        result.collected += 1
        if changed:
            result.changed += 1
            if len(result.changes) < _MAX_STORED_CHANGES:
                result.changes.append(change)

    if not result.dry_run:
        _save_poll_notes(notes, now)
        db.session.commit()


def _load_targets(
    *,
    limit: int | None,
    device_ids: list[int] | tuple[int, ...] | None,
) -> list[_Target]:
    stmt = select(Device).order_by(Device.id)
    if device_ids:
        stmt = stmt.where(Device.id.in_(list(device_ids)))
    rows = list(db.session.scalars(stmt))
    targets: list[_Target] = []
    for row in rows:
        ip = (row.ip or "").strip()
        if not ip:
            continue
        if not device_ids and not is_windows_hardware_target(row):
            continue
        targets.append(
            _Target(
                device_id=row.id,
                ip=ip,
                hostname=row.hostname,
                serial_number=row.serial_number,
            )
        )
        if limit is not None and len(targets) >= max(0, int(limit)):
            break
    return targets


def _probe_targets(
    targets: list[_Target],
    creds: discovery_service.DiscoveryCredentials,
    bad_credentials: tuple[str, str],
) -> list[_Probe]:
    probes: list[_Probe] = []
    with ThreadPoolExecutor(max_workers=_SWEEP_WORKERS) as pool:
        futures = [
            pool.submit(_probe_one, target, creds, bad_credentials) for target in targets
        ]
        for future in futures:
            try:
                probes.append(future.result())
            except Exception:
                logger.exception("Рабочий поток опроса железа завершился с ошибкой")
    return probes


def _probe_one(
    target: _Target,
    creds: discovery_service.DiscoveryCredentials,
    bad_credentials: tuple[str, str],
) -> _Probe:
    """Ping, служба badagent, lookup bAD. Сессию SQLAlchemy сюда не передаём.

    WMI процессора, памяти, дисков и версии Windows не вызывается.
    """
    from app.services.bad_hardware import card_from_lookup
    from app.services.bad_integration import lookup_with_credentials

    try:
        ping = ping_host(target.ip)
        if ping.status != DeviceStatus.ONLINE:
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=False,
                note="Нет ответа на ping. Службу badagent и lookup не вызывали.",
            )
        agent = discovery_service.lookup_badagent(target.ip, creds=creds)
        if agent.wmi_failed:
            why = agent.detail or "WMI не открылся"
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                error="службу badagent не прочитали",
                note=(
                    f"Ping есть. Удалённый WMI не открылся ({why}). "
                    "Службу badagent не проверили. Нужна учётка из «Опрос сети» "
                    "и доступ к RPC (порт 135)."
                ),
            )
        if not agent.service_present:
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                no_agent=True,
                note=(
                    "Ping есть. Служба badagent не установлена. "
                    "Lookup в bAD не вызывали, новый снимок не записан."
                ),
            )
        if not agent.machine_guid:
            why = agent.detail or "device_id не прочитан"
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                error="device_id не прочитан",
                note=(
                    "Ping есть. Служба badagent есть. "
                    f"{why} Lookup не вызывали. device_id в карточку не пишется."
                ),
            )
        looked = lookup_with_credentials(
            bad_credentials[0], bad_credentials[1], agent.machine_guid
        )
        found = _identity_phrase(agent)
        if looked.error == "revoked" or looked.status == 401:
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                revoked=True,
                error="ключ интеграции не активен",
                note=(
                    f"Ping есть. Служба badagent есть, {found}. "
                    "Lookup bAD ответил 401: ключ интеграции не принят."
                ),
            )
        if looked.error == "not_connected":
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                error="ключ интеграции не активен",
                note="Ключ интеграции уже не активен. Lookup не выполнен.",
            )
        if looked.status == 404:
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                unregistered=True,
                note=(
                    f"Ping есть. Служба badagent есть, {found}. "
                    "Lookup bAD ответил 404: этой машины в bAD нет. "
                    "device_id в карточку не записан."
                ),
            )
        if looked.status != 200 or not isinstance(looked.data, dict):
            status = looked.status or "нет ответа"
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                error=f"lookup bAD: {status}",
                note=(
                    f"Ping есть. Служба badagent есть, {found}. "
                    f"Lookup bAD: {status}. Снимок не записан."
                ),
            )
        card = card_from_lookup(looked.data)
        if not card.bad_id and (card.snapshot is None or card.snapshot.is_empty):
            return _Probe(
                device_id=target.device_id,
                ip=target.ip,
                online=True,
                error="в ответе lookup нет карточки",
                note=(
                    f"Ping есть. Служба badagent есть, {found}. "
                    "Lookup bAD ответил 200, но без bad_id и без железа."
                ),
            )
        return _Probe(
            device_id=target.device_id,
            ip=target.ip,
            online=True,
            snapshot=card.snapshot,
            serial_number=card.serial_number,
            hostname=card.hostname,
            bad_id=card.bad_id,
        )
    except Exception as exc:
        logger.debug("hardware poll: %s — %s", target.ip, type(exc).__name__)
        return _Probe(
            device_id=target.device_id,
            ip=target.ip,
            online=False,
            error="сбой опроса",
            note=f"Сбой опроса ({type(exc).__name__}). Снимок не записан.",
        )


def _identity_phrase(agent: discovery_service.BadAgentProbe) -> str:
    """Откуда взяли device_id, без самого значения."""
    if agent.id_source == "config":
        return "device_id взят из конфига агента"
    if agent.id_source == "registry":
        extra = f" {agent.detail}" if agent.detail else ""
        return "device_id взят из реестра MachineGuid." + extra
    return "device_id прочитан"


def _save_poll_notes(notes: dict[int, str], when) -> None:
    """Последняя попытка на карточке. Значение MachineGuid сюда не попадает."""
    if not notes:
        return
    rows = list(db.session.scalars(select(Device).where(Device.id.in_(list(notes)))))
    for device in rows:
        text = " ".join((notes.get(device.id) or "").split())
        if not text:
            continue
        device.hardware_poll_at = when
        device.hardware_poll_note = text[:500]


def _mark_unregistered(device_ids: list[int]) -> None:
    """Агент есть, в bAD устройства нет. Снимок железа не трогаем."""
    rows = list(db.session.scalars(select(Device).where(Device.id.in_(device_ids))))
    for device in rows:
        device.bad_id = None
        device.bad_agent_status = "unregistered"


def _assign_bad_id(owner: Device, bad_id: str) -> None:
    """Единственная привязка к bAD. device_id на карточку не пишем."""
    others = list(
        db.session.scalars(
            select(Device).where(Device.bad_id == bad_id, Device.id != owner.id)
        )
    )
    for other in others:
        other.bad_id = None
    owner.bad_id = bad_id
    owner.bad_agent_status = None


def _active_run(now) -> HardwarePollRun | None:
    cutoff = as_utc(now) - _STALE_POLL_AFTER
    return db.session.scalars(
        select(HardwarePollRun)
        .where(
            HardwarePollRun.finished_at.is_(None),
            HardwarePollRun.started_at >= cutoff,
        )
        .order_by(HardwarePollRun.id.desc())
        .limit(1)
    ).first()


def _close_stale_runs(now) -> None:
    cutoff = as_utc(now) - _STALE_POLL_AFTER
    stale = list(
        db.session.scalars(
            select(HardwarePollRun).where(
                HardwarePollRun.finished_at.is_(None),
                HardwarePollRun.started_at < cutoff,
            )
        )
    )
    if not stale:
        return
    for run in stale:
        run.finished_at = now
        run.error = run.error or "прерван: прогон завис"
    db.session.commit()
