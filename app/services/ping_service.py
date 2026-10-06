"""ICMP-пинг списка адресов и запись результата в БД.

Как устроен один проход poll_all_sectors
----------------------------------------
1. Главный поток читает секторы и разворачивает CIDR в IP.
   Сессия SQLAlchemy живёт только здесь: она не потокобезопасна,
   один и тот же db.session нельзя отдать рабочим потокам.
2. Главный поток один раз читает учётку WMI (app_settings / .env).
3. ThreadPoolExecutor вызывает только ping_host и функции discovery.
   Воркер получает IP и заранее загруженные creds, возвращает Probe.
   Объектов ORM в потоке нет. Строку devices заранее не создаём:
   пустой адрес на карте не нужен. Для серых онлайн-адресов (нет своего
   имени и нет WMI-серийника) тот же воркер делает короткий TCP/SNMP
   отпечаток — не отдельный проход по всей сети.
4. Снова главный поток:
   * офлайн по уже известному IP → статус адреса и device_history;
     если у машины есть другой онлайн-интерфейс — основной IP
     переключается на него, карточка остаётся online;
   * онлайн → ключ идентичности только serial_number (WMI). Hostname
     на карте, не для слияния. Без SN можно переиспользовать строку
     с тем же IP и пустым серийником (камеры, Linux, WMI не ответил).
     Смена DHCP-адреса при том же SN обновляет IP/сектор у той же
     строки. Другие карточки с этим IP (короткая аренда VPN) → offline.
     При появлении SN сливаются призраки без SN с тем же IP или
     WMI-hostname. Все виденные IP пишутся в device_addresses
     (Ethernet + Wi‑Fi одной машины).
     MAC сначала из ARP, иначе из WMI. Имя: Win32_ComputerSystem,
     PTR только если WMI молчит и у строки ещё нет имени.
     Текущая УЗ пишется в endpoint_accounts / device_account_history.
5. После прохода — опрос железа для впервые созданных Windows-карточек
   (не ждать полуденный cron).

Позже ту же функцию run_network_poll / poll_all_sectors вызовет задача Celery.
Менять разбор пинга и запись истории для этого не нужно.
"""

import logging
import os
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, NetworkPollRun, Sector
from app.services import discovery_service, fingerprint_service
from app.services.device_kind import needs_fingerprint
from app.services.net_utils import NetworkInputError, assert_public_ipv4, expand_ranges
from app.utils import as_utc, utcnow

logger = logging.getLogger(__name__)

# Сколько пингов одновременно. Дальше упираемся в сеть и в ОС,
# а не в Python. Запись в БД всё равно идёт одним потоком после пула.
_MAX_WORKERS = 32

# Один проход опроса на процесс; для веб + scheduler — ещё проверка незавершённой строки.
_poll_run_lock = threading.Lock()
_STALE_POLL_AFTER = timedelta(hours=2)
_RECENT_POLL_RUNS_LIMIT = 20


class PollInProgressError(RuntimeError):
    """Опрос уже идёт (этот процесс или незавершённая строка в БД)."""

# tracert/traceroute на 15 прыжков. Потолок процесса — около 40 с,
# чтобы зависшая трасса не держала запрос карточки устройства бесконечно.
_TRACE_TIMEOUT_S = 40

# Английский ping: "time=12 ms" или "time<1ms".
# Русский ping:    "время=12мс" или "время<1мс".
# Запятая в числе ("12,3") тоже встречается в локализованном выводе.
_RESPONSE_TIME_RE = re.compile(
    r"(?:time|время)\s*[=<]\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)


@dataclass
class PingResult:
    status: str  # DeviceStatus.ONLINE ("online") или OFFLINE ("offline")
    response_time_ms: int | None
    output: str


@dataclass
class _Probe:
    """Результат одного адреса. Это не строка БД, его можно собрать в потоке."""

    ip: str
    result: PingResult
    hostname: str | None
    mac: str | None
    serial_number: str | None
    logged_on_user: str | None = None
    hostname_from_wmi: bool = False
    fingerprint_kind: str | None = None
    fingerprint_detail: str | None = None


def ping_host(
    ip: str,
    timeout_s: int = 1,
    on_output=None,
    cancel_event: threading.Event | None = None,
    on_process=None,
) -> PingResult:
    """Один ICMP-запрос. Команду собираем только из проверенного адреса.

    На Windows ключ -w — миллисекунды, поэтому timeout_s умножается на 1000.
    На Linux у iputils -W — секунды, без умножения.
    """
    ip = assert_public_ipv4(ip)
    if os.name == "nt":
        argv = ["ping", "-n", "1", "-w", str(timeout_s * 1000), ip]
    else:
        argv = ["ping", "-c", "1", "-W", str(timeout_s), ip]
    if on_output is not None or cancel_event is not None or on_process is not None:
        try:
            rc, output = _stream_command(
                argv,
                timeout_s + 2,
                on_output,
                cancel_event,
                on_process,
                "Превышено время ожидания ping.",
            )
        except OSError as exc:
            return PingResult(DeviceStatus.OFFLINE, None, str(exc))
        if rc != 0:
            if not output:
                output = f"ping завершился с кодом {rc}"
            return PingResult(DeviceStatus.OFFLINE, None, output)
        return PingResult(DeviceStatus.ONLINE, _parse_response_time_ms(output), output)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s + 2,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return PingResult(DeviceStatus.OFFLINE, None, _error_text(exc))
    except OSError as exc:
        return PingResult(DeviceStatus.OFFLINE, None, str(exc))

    output = _combined(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        if not output:
            output = f"ping завершился с кодом {completed.returncode}"
        return PingResult(DeviceStatus.OFFLINE, None, output)
    return PingResult(DeviceStatus.ONLINE, _parse_response_time_ms(output), output)


def trace_host(
    ip: str,
    on_output=None,
    cancel_event: threading.Event | None = None,
    on_process=None,
) -> tuple[int, str]:
    """Трассировка с этого сервера, не PsExec и не удалённая команда.

    Возвращает (код возврата, stdout и stderr вместе).
    Если программы нет в PATH — код 127 и текст, что поставить.
    """
    ip = assert_public_ipv4(ip)
    if os.name == "nt":
        argv = ["tracert", "-d", "-h", "15", "-w", "1000", ip]
    else:
        argv = ["traceroute", "-n", "-m", "15", "-w", "1", "-q", "1", ip]
    if on_output is not None or cancel_event is not None or on_process is not None:
        try:
            return _stream_command(
                argv,
                _TRACE_TIMEOUT_S,
                on_output,
                cancel_event,
                on_process,
                "Превышено время ожидания трассировки.",
            )
        except FileNotFoundError:
            return (
                127,
                "Не найдена программа трассировки. "
                "На Linux установите пакет traceroute, на Windows используйте tracert.",
            )
        except OSError as exc:
            return 1, str(exc)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_TRACE_TIMEOUT_S,
            shell=False,
        )
    except FileNotFoundError:
        return (
            127,
            "Не найдена программа трассировки. "
            "На Linux установите пакет traceroute, на Windows используйте tracert.",
        )
    except subprocess.TimeoutExpired as exc:
        return 124, _error_text(exc) or "Превышено время ожидания трассировки."
    except OSError as exc:
        return 1, str(exc)
    return completed.returncode, _combined(completed.stdout, completed.stderr)


def _stream_command(
    argv: list[str],
    timeout_s: int,
    on_output,
    cancel_event: threading.Event | None,
    on_process,
    timeout_message: str,
) -> tuple[int, str]:
    """Читает stdout построчно, чтобы страница лога обновлялась до конца команды."""
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=False,
    )
    if on_process is not None:
        on_process(proc)
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        try:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    chunks: list[str] = []
    deadline = time.monotonic() + timeout_s
    timed_out = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            if proc.poll() is None:
                proc.terminate()
            break
        if time.monotonic() > deadline:
            timed_out = True
            if proc.poll() is None:
                proc.kill()
            chunks.append(timeout_message + "\n")
            break
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        chunks.append(line)
        if on_output is not None:
            on_output(line)
    try:
        rc = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = proc.wait(timeout=5)
    output = "".join(chunks)
    if timed_out and timeout_message not in output:
        output = (output + "\n" + timeout_message).strip()
    return rc if rc is not None else 1, output


def poll_all_sectors() -> dict[str, int]:
    """Один проход по всем секторам.

    Вызывать внутри app.app_context(): expand_ranges читает лимит из
    конфигурации приложения, запись идёт через db.session.

    Сводка:
    scanned — сколько адресов пинговали и попытались записать;
    online / offline — из них, чья запись в БД прошла
      (offline считается только для уже известных машин);
    errors — битый диапазон сектора, сбой рабочего потока или ошибка COMMIT;
    discovered — сколько новых карточек создано;
    hardware_polled — сколько из них прошли опрос железа сразу после.

    Для журнала прогонов и защиты от параллели вызывайте run_network_poll.
    """
    stats = {
        "scanned": 0,
        "online": 0,
        "offline": 0,
        "errors": 0,
        "discovered": 0,
        "hardware_polled": 0,
    }
    assignments = _collect_assignments(stats)
    if not assignments:
        return stats

    probes = _probe_all(list(assignments), stats)
    # Сначала онлайн: если машина сменила IP в этом же проходе,
    # строка devices уже уедет на новый адрес, и офлайн по старому
    # IP не запишет лишнюю историю.
    ordered = sorted(
        probes,
        key=lambda item: 0 if item.result.status == DeviceStatus.ONLINE else 1,
    )
    new_device_ids: list[int] = []
    for probe in ordered:
        stats["scanned"] += 1
        sector_id = assignments[probe.ip]
        if probe.result.status == DeviceStatus.ONLINE:
            ok, created_id = _save_online_probe(probe, sector_id)
            if not ok:
                stats["errors"] += 1
                continue
            stats["online"] += 1
            if created_id is not None:
                new_device_ids.append(created_id)
            continue
        updated = _save_offline_probe(probe)
        if updated is None:
            stats["errors"] += 1
            continue
        # Пустой адрес не создаём и в offline не считаем.
        if updated:
            stats["offline"] += 1

    stats["discovered"] = len(new_device_ids)
    if new_device_ids:
        stats["hardware_polled"] = _poll_hardware_for_discovered(new_device_ids)

    logger.info(
        "Опрос секторов: проверено %s, онлайн %s, офлайн %s, "
        "новых %s, железо сразу %s, ошибок %s",
        stats["scanned"],
        stats["online"],
        stats["offline"],
        stats["discovered"],
        stats["hardware_polled"],
        stats["errors"],
    )
    return stats


def _poll_hardware_for_discovered(device_ids: list[int]) -> int:
    """Сразу после появления карточки — опрос железа (SN/CPU/ОЗУ/диски/ОС).

    Не ждём полуденный cron: иначе смена IP на следующий день до железа
    оставляет «пустую» карточку и плодит призраков.
    """
    from app.services.hardware_poll_service import (
        HardwarePollError,
        HardwarePollInProgressError,
        is_windows_hardware_target,
        run_hardware_poll,
    )

    unique_ids = list(dict.fromkeys(int(item) for item in device_ids))
    if not unique_ids:
        return 0
    devices = list(
        db.session.scalars(select(Device).where(Device.id.in_(unique_ids)))
    )
    targets = [
        device.id
        for device in devices
        if is_windows_hardware_target(device) and device.hardware_checked_at is None
    ]
    if not targets:
        return 0
    try:
        result = run_hardware_poll(mode="on_discover", device_ids=targets)
    except HardwarePollInProgressError:
        logger.info(
            "Опрос железа для новых устройств отложен: уже выполняется (%s id)",
            len(targets),
        )
        return 0
    except HardwarePollError as exc:
        logger.warning("Опрос железа для новых устройств пропущен: %s", exc)
        return 0
    except Exception:
        logger.exception("Сбой опроса железа для новых устройств %s", targets)
        return 0
    return int(result.collected or 0)


def count_poll_runs() -> int:
    """Число записей в network_poll_runs."""
    return int(db.session.scalar(select(func.count()).select_from(NetworkPollRun)) or 0)


def load_recent_poll_runs(
    limit: int = _RECENT_POLL_RUNS_LIMIT,
    *,
    offset: int = 0,
) -> list[NetworkPollRun]:
    """Последние прогоны опроса, новые сверху.

    offset — для пагинации на странице настроек (CSV по-прежнему берёт limit).
    """
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset or 0))
    return list(
        db.session.scalars(
            select(NetworkPollRun)
            .order_by(NetworkPollRun.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


def run_network_poll(*, mode: str = "scheduled") -> dict[str, int]:
    """Опрос с записью в network_poll_runs. Без параллельных запусков.

    mode: scheduled (планировщик), manual (кнопка в Параметрах), cli (flask poll).
    """
    if not _poll_run_lock.acquire(blocking=False):
        raise PollInProgressError("Опрос сети уже выполняется")
    started = utcnow()
    run: NetworkPollRun | None = None
    try:
        _close_stale_poll_runs(started)
        active = _active_poll_run(started)
        if active is not None:
            raise PollInProgressError(
                "Опрос сети уже выполняется (другой процесс)."
            )
        run = NetworkPollRun(started_at=started, mode=mode or "scheduled")
        db.session.add(run)
        db.session.commit()

        try:
            stats = poll_all_sectors()
        except Exception as exc:  # noqa: BLE001
            run.finished_at = utcnow()
            run.error = str(exc)[:2000]
            db.session.commit()
            raise

        run.finished_at = utcnow()
        run.scanned = int(stats.get("scanned") or 0)
        run.online = int(stats.get("online") or 0)
        run.offline = int(stats.get("offline") or 0)
        run.errors = int(stats.get("errors") or 0)
        db.session.commit()
        return stats
    finally:
        _poll_run_lock.release()


def _active_poll_run(now) -> NetworkPollRun | None:
    """Незавершённый прогон младше порога устаревания."""
    cutoff = as_utc(now) - _STALE_POLL_AFTER
    return db.session.scalars(
        select(NetworkPollRun)
        .where(NetworkPollRun.finished_at.is_(None))
        .where(NetworkPollRun.started_at >= cutoff)
        .order_by(NetworkPollRun.id.desc())
        .limit(1)
    ).first()


def _close_stale_poll_runs(now) -> None:
    """Пометить зависшие прогоны (процесс убит) как завершённые с ошибкой."""
    cutoff = as_utc(now) - _STALE_POLL_AFTER
    stale = list(
        db.session.scalars(
            select(NetworkPollRun)
            .where(NetworkPollRun.finished_at.is_(None))
            .where(NetworkPollRun.started_at < cutoff)
        )
    )
    if not stale:
        return
    for row in stale:
        row.finished_at = now
        row.error = row.error or "Прогон прерван (устаревший незавершённый запуск)."
    db.session.commit()


def _collect_assignments(stats: dict[str, int]) -> dict[str, int]:
    """IP → sector_id. Секторы идут по id, при пересечении побеждает поздний.

    Словарь, а не список: один адрес пингуем один раз, даже если он
    вписан в два сектора.

    Пересечения не пишем по одному IP: на большом CIDR это тысячи строк
    за один проход и снова каждые POLL_INTERVAL_SECONDS. Одна сводка
    на проход — сколько адресов и какие пары секторов.
    """
    sectors = db.session.scalars(
        select(Sector).options(selectinload(Sector.ranges)).order_by(Sector.id)
    ).all()
    assigned: dict[str, int] = {}
    # (проигравший sector_id, победитель) → число адресов.
    overlaps: dict[tuple[int, int], int] = {}
    overlap_example: str | None = None
    for sector in sectors:
        cidrs = [item.cidr for item in sector.ranges]
        try:
            ips = expand_ranges(cidrs)
        except NetworkInputError as exc:
            logger.warning(
                "Сектор %s (%s): диапазон пропущен: %s",
                sector.id,
                sector.name,
                exc,
            )
            stats["errors"] += 1
            continue
        for ip in ips:
            previous = assigned.get(ip)
            if previous is not None and previous != sector.id:
                key = (previous, sector.id)
                overlaps[key] = overlaps.get(key, 0) + 1
                if overlap_example is None:
                    overlap_example = (
                        f"{ip} (секторы {previous} → {sector.id})"
                    )
            assigned[ip] = sector.id
    if overlaps:
        total = sum(overlaps.values())
        pairs = ", ".join(
            f"{loser}↔{winner}: {count}"
            for (loser, winner), count in sorted(overlaps.items())
        )
        logger.warning(
            "Пересечение диапазонов: %s адресов в нескольких секторах "
            "(%s). Пример: %s. При пересечении побеждает сектор с большим id.",
            total,
            pairs,
            overlap_example or "—",
        )
    return assigned


def _probe_all(ips: list[str], stats: dict[str, int]) -> list[_Probe]:
    """Пинг и discovery в пуле. Сессию здесь не передаём и не открываем.

    Учётку WMI читаем один раз в главном потоке (есть app context / БД)
    и передаём в воркеры: у ThreadPoolExecutor своего Flask-контекста нет,
    иначе UI-учётка из app_settings молча не подхватывается.

    with у ThreadPoolExecutor дожидается потоков. К return снова работает
    только главный поток — можно писать в БД.
    """
    wmi_creds = discovery_service.discovery_credentials()
    if wmi_creds is None:
        logger.warning(
            "Учётка WMI не задана: серийник, имя и MAC по WMI не запрашиваются "
            "(Настройки → Опросы ПК или DISCOVERY_* в .env). "
            "Без серийника устройство не склеивается по имени — только заглушка по IP."
        )
    else:
        logger.info(
            "WMI-опрос: учётка %s\\%s",
            wmi_creds.domain or ".",
            wmi_creds.username,
        )

    probes: list[_Probe] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = [pool.submit(_probe, ip, wmi_creds) for ip in ips]
        for future in futures:
            try:
                probes.append(future.result())
            except Exception:
                logger.exception("Рабочий поток опроса завершился с ошибкой")
                stats["errors"] += 1
    return probes


def _probe(
    ip: str,
    wmi_creds: discovery_service.DiscoveryCredentials | None = None,
) -> _Probe:
    """Только сеть. Любой db.session в этой функции будет гонкой.

    wmi_creds — заранее прочитанная учётка с главного потока (или None).
    """
    result = ping_host(ip)
    hostname = None
    mac = None
    serial_number = None
    logged_on_user = None
    hostname_from_wmi = False
    fingerprint_kind = None
    fingerprint_detail = None
    if result.status == DeviceStatus.ONLINE:
        # Ищем атрибут на модуле в момент вызова, а не копией функции:
        # так подмена в тестах (и будущий кэш DNS) видна без правки этого файла.
        ptr = discovery_service.lookup_hostname(ip)
        # ARP — только свой L2. Через маршрутизатор MAC доберём из WMI.
        mac = discovery_service.lookup_mac(ip)
        inventory = discovery_service.lookup_wmi_inventory(ip, creds=wmi_creds)
        serial_number = inventory.serial_number
        logged_on_user = inventory.logged_on_user
        if not mac and inventory.mac:
            mac = inventory.mac
        hostname_from_wmi = bool(inventory.hostname)
        hostname = discovery_service.preferred_hostname(
            os_hostname=inventory.hostname,
            ptr=ptr,
        )
        if needs_fingerprint(hostname, serial_number):
            try:
                fingerprint = fingerprint_service.fingerprint_host(ip)
                fingerprint_kind = fingerprint.kind
                fingerprint_detail = fingerprint.detail
            except Exception:
                logger.exception("Не удалось снять TCP/SNMP-отпечаток %s", ip)
    return _Probe(
        ip=ip,
        result=result,
        hostname=hostname,
        mac=mac,
        serial_number=serial_number,
        logged_on_user=logged_on_user,
        hostname_from_wmi=hostname_from_wmi,
        fingerprint_kind=fingerprint_kind,
        fingerprint_detail=fingerprint_detail,
    )


def _save_online_probe(probe: _Probe, sector_id: int) -> tuple[bool, int | None]:
    """Найти машину по serial (иначе IP без SN) и записать онлайн + историю.

    Возвращает (ok, created_device_id). created_device_id — только если
    в этом вызове создали новую строку devices.
    """
    from app.services.account_service import apply_logged_on_user
    from app.services.device_identity_service import absorb_ghosts, upsert_device_address

    existing = _find_existing_device(
        ip=probe.ip,
        serial_number=probe.serial_number,
    )
    created = existing is None
    device = existing or Device(
        ip=probe.ip,
        sector_id=sector_id,
        hostname=probe.hostname,
        serial_number=probe.serial_number,
        last_status=DeviceStatus.UNKNOWN,
    )
    if created:
        db.session.add(device)

    device.ip = probe.ip
    device.sector_id = sector_id
    device.last_status = DeviceStatus.ONLINE
    device.last_response_time_ms = probe.result.response_time_ms
    device.last_seen = utcnow()
    if probe.hostname:
        # PTR не затирает уже известное имя с машины.
        if probe.hostname_from_wmi or not device.hostname:
            device.hostname = probe.hostname
    if probe.mac:
        device.mac = probe.mac
    if probe.serial_number:
        device.serial_number = probe.serial_number
    if probe.fingerprint_kind:
        device.fingerprint_kind = probe.fingerprint_kind
        if probe.fingerprint_detail:
            device.fingerprint_detail = probe.fingerprint_detail

    try:
        # Сначала id новой строки, потом история с внешним ключом.
        db.session.flush()
        created_id = device.id if created else None

        upsert_device_address(
            device,
            ip=probe.ip,
            mac=probe.mac,
            status=DeviceStatus.ONLINE,
        )

        # VPN/DHCP: этот SN сейчас на адресе — остальные карточки с тем же IP
        # больше здесь не живут (иначе опрос железа снимет чужой ПК).
        if probe.serial_number:
            _displace_other_ip_holders(device, probe.ip)
            absorb_ghosts(
                device,
                ip=probe.ip,
                hostname=probe.hostname if probe.hostname_from_wmi else None,
                hostname_trusted=bool(probe.hostname_from_wmi),
            )

        db.session.add(
            DeviceHistory(
                device_id=device.id,
                timestamp=utcnow(),
                status=DeviceStatus.ONLINE,
                response_time_ms=probe.result.response_time_ms,
            )
        )
        # None — WMI не ответил: текущую УЗ не трогаем.
        # "" — никто не залогинен; иначе DOMAIN\user.
        if probe.logged_on_user is not None:
            try:
                with db.session.begin_nested():
                    apply_logged_on_user(device, probe.logged_on_user or None)
            except Exception:
                logger.exception("Не удалось записать УЗ для %s", probe.ip)
        db.session.commit()
    except Exception:
        logger.exception("Не удалось сохранить онлайн-опрос %s", probe.ip)
        db.session.rollback()
        return False, None
    return True, created_id


def _save_offline_probe(probe: _Probe) -> bool | None:
    """Офлайн пишем только если машина уже известна по этому IP.

    True — обновили хотя бы одну строку; False — пустой адрес, ничего не создаём;
    None — ошибка COMMIT.

    Учитывает device_addresses: один интерфейс offline не роняет карточку,
    если другой интерфейс той же машины ещё online.
    """
    from app.services.device_identity_service import mark_address_offline

    devices = list(db.session.scalars(select(Device).where(Device.ip == probe.ip)))
    address_owners = mark_address_offline(probe.ip)
    by_id = {device.id: device for device in devices}
    for device in address_owners:
        by_id.setdefault(device.id, device)
    devices = list(by_id.values())
    if not devices:
        return False
    now = utcnow()
    for device in devices:
        # mark_address_offline уже мог оставить ONLINE на другом интерфейсе.
        if device.last_status == DeviceStatus.OFFLINE:
            device.last_response_time_ms = None
        db.session.add(
            DeviceHistory(
                device_id=device.id,
                timestamp=now,
                status=device.last_status or DeviceStatus.OFFLINE,
                response_time_ms=None,
            )
        )
    try:
        db.session.commit()
    except Exception:
        logger.exception("Не удалось сохранить офлайн-опрос %s", probe.ip)
        db.session.rollback()
        return None
    return True


def _find_existing_device(
    *,
    ip: str,
    serial_number: str | None,
) -> Device | None:
    """Найти карточку без создания. Ключ — serial, иначе IP без SN."""
    if serial_number:
        found = db.session.scalar(
            select(Device).where(Device.serial_number == serial_number)
        )
        if found is not None:
            return found

    return db.session.scalars(
        select(Device)
        .where(Device.ip == ip, Device.serial_number.is_(None))
        .order_by(Device.id)
    ).first()


def _resolve_device(
    *,
    ip: str,
    hostname: str | None,
    serial_number: str | None,
    sector_id: int,
) -> Device:
    """Ключ идентичности — только serial_number.

    Hostname на карте (отображение), PTR/DNS в слиянии не участвуют.
    При том же SN смена IP обновляет эту строку — DHCP не плодит дубли.
    Без серийника можно переиспользовать только запись с тем же IP
    и пустым SN (камера, Linux, WMI не ответил). Строку с чужим SN
    не трогаем: иначе чужой хост на старом адресе перезапишет карточку.
    SN позже на новом IP: если строка с этим SN уже есть — она;
    призрак без SN на старом IP сливается отдельно (absorb_ghosts).
    """
    found = _find_existing_device(ip=ip, serial_number=serial_number)
    if found is not None:
        return found

    device = Device(
        ip=ip,
        sector_id=sector_id,
        hostname=hostname,
        serial_number=serial_number,
        last_status=DeviceStatus.UNKNOWN,
    )
    db.session.add(device)
    return device


def _displace_other_ip_holders(owner: Device, ip: str) -> None:
    """Пометить offline другие карточки, у которых ещё записан этот IP.

    Сам адрес не чистим: это последний известный. Но статус ONLINE у
    предыдущего владельца (короткая аренда VPN) оставлять нельзя.
    """
    others = list(
        db.session.scalars(
            select(Device).where(Device.ip == ip, Device.id != owner.id)
        )
    )
    if not others:
        return
    now = utcnow()
    for other in others:
        if other.last_status == DeviceStatus.OFFLINE:
            continue
        other.last_status = DeviceStatus.OFFLINE
        other.last_response_time_ms = None
        db.session.add(
            DeviceHistory(
                device_id=other.id,
                timestamp=now,
                status=DeviceStatus.OFFLINE,
                response_time_ms=None,
            )
        )


def check_device(device: Device, *, timeout_s: int = 1) -> dict:
    """Разовый ICMP по уже известному устройству: статус + device_history.

    В отличие от diagnostic Ping (script_runs), пишет в историю опросов
    и сразу обновляет last_status / last_seen / last_response_time_ms.
    WMI/discovery не вызываются — только доступность.
    """
    if device is None or not getattr(device, "id", None):
        raise ValueError("Устройство не задано.")
    ip = assert_public_ipv4(device.ip)
    previous = device.last_status or DeviceStatus.UNKNOWN
    result = ping_host(ip, timeout_s=timeout_s)
    now = utcnow()
    status = result.status
    changed = status != previous

    device.last_status = status
    device.last_response_time_ms = result.response_time_ms
    if status == DeviceStatus.ONLINE:
        device.last_seen = now
    db.session.add(
        DeviceHistory(
            device_id=device.id,
            timestamp=now,
            status=status,
            response_time_ms=result.response_time_ms,
        )
    )
    db.session.commit()

    return {
        "device_id": device.id,
        "ip": device.ip,
        "status": status,
        "previous_status": previous,
        "changed": changed,
        "response_time_ms": result.response_time_ms,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
        "checked_at": now.isoformat(),
    }


def _parse_response_time_ms(output: str) -> int | None:
    """Первое время ответа из текста ping. None, если строки time/время нет.

    time<1ms сохраняем как 1: в истории целое число миллисекунд,
    а не диапазон «меньше единицы».
    """
    match = _RESPONSE_TIME_RE.search(output or "")
    if match is None:
        return None
    raw = match.group(1).replace(",", ".")
    return int(round(float(raw)))


def _combined(stdout: str | None, stderr: str | None) -> str:
    parts = [part for part in (stdout, stderr) if part]
    return "\n".join(parts)


def _error_text(exc: subprocess.TimeoutExpired) -> str:
    """Текст таймаута плюс кусок вывода, который ping успел напечатать."""
    stdout = exc.stdout or ""
    stderr = exc.stderr or ""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    partial = _combined(stdout, stderr)
    message = str(exc)
    if partial and message:
        return f"{partial}\n{message}"
    return partial or message
