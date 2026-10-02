"""ICMP-пинг списка адресов и запись результата в БД.

Как устроен один проход poll_all_sectors
----------------------------------------
1. Главный поток читает секторы и разворачивает CIDR в IP.
   Сессия SQLAlchemy живёт только здесь: она не потокобезопасна,
   один и тот же db.session нельзя отдать рабочим потокам.
2. ThreadPoolExecutor вызывает только ping_host и функции discovery.
   Воркер получает строку IP и возвращает обычный объект Probe.
   Объектов ORM в потоке нет. Строку devices заранее не создаём:
   пустой адрес на карте не нужен.
3. Снова главный поток:
   * офлайн по уже известному IP → статус и device_history;
   * онлайн → serial+MAC (WMI) / hostname / IP как ключ идентичности,
     обновление или создание одной машины, история сохраняется
     при смене адреса и сектора. MAC сначала из ARP, иначе из WMI.

Позже ту же функцию poll_all_sectors вызовет задача Celery. Менять
разбор пинга и запись истории для этого не нужно.
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

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, Sector
from app.services import discovery_service
from app.services.net_utils import NetworkInputError, assert_public_ipv4, expand_ranges
from app.utils import utcnow

logger = logging.getLogger(__name__)

# Сколько пингов одновременно. Дальше упираемся в сеть и в ОС,
# а не в Python. Запись в БД всё равно идёт одним потоком после пула.
_MAX_WORKERS = 32

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
    errors — битый диапазон сектора, сбой рабочего потока или ошибка COMMIT.
    """
    stats = {"scanned": 0, "online": 0, "offline": 0, "errors": 0}
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
    for probe in ordered:
        stats["scanned"] += 1
        sector_id = assignments[probe.ip]
        if probe.result.status == DeviceStatus.ONLINE:
            if not _save_online_probe(probe, sector_id):
                stats["errors"] += 1
                continue
            stats["online"] += 1
            continue
        updated = _save_offline_probe(probe)
        if updated is None:
            stats["errors"] += 1
            continue
        # Пустой адрес не создаём и в offline не считаем.
        if updated:
            stats["offline"] += 1

    logger.info(
        "Опрос секторов: проверено %s, онлайн %s, офлайн %s, ошибок %s",
        stats["scanned"],
        stats["online"],
        stats["offline"],
        stats["errors"],
    )
    return stats


def _collect_assignments(stats: dict[str, int]) -> dict[str, int]:
    """IP → sector_id. Секторы идут по id, при пересечении побеждает поздний.

    Словарь, а не список: один адрес пингуем один раз, даже если он
    вписан в два сектора.
    """
    sectors = db.session.scalars(select(Sector).order_by(Sector.id)).all()
    assigned: dict[str, int] = {}
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
                logger.warning(
                    "Адрес %s входит в секторы %s и %s. "
                    "Оставляем %s: при пересечении побеждает сектор с большим id.",
                    ip,
                    previous,
                    sector.id,
                    sector.id,
                )
            assigned[ip] = sector.id
    return assigned


def _probe_all(ips: list[str], stats: dict[str, int]) -> list[_Probe]:
    """Пинг и discovery в пуле. Сессию здесь не передаём и не открываем.

    with у ThreadPoolExecutor дожидается потоков. К return снова работает
    только главный поток — можно писать в БД.
    """
    probes: list[_Probe] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = [pool.submit(_probe, ip) for ip in ips]
        for future in futures:
            try:
                probes.append(future.result())
            except Exception:
                logger.exception("Рабочий поток опроса завершился с ошибкой")
                stats["errors"] += 1
    return probes


def _probe(ip: str) -> _Probe:
    """Только сеть. Любой db.session в этой функции будет гонкой."""
    result = ping_host(ip)
    hostname = None
    mac = None
    serial_number = None
    if result.status == DeviceStatus.ONLINE:
        # Ищем атрибут на модуле в момент вызова, а не копией функции:
        # так подмена в тестах (и будущий кэш DNS) видна без правки этого файла.
        hostname = discovery_service.lookup_hostname(ip)
        # ARP — только свой L2. Через маршрутизатор MAC доберём из WMI.
        mac = discovery_service.lookup_mac(ip)
        inventory = discovery_service.lookup_wmi_inventory(ip)
        serial_number = inventory.serial_number
        if not mac and inventory.mac:
            mac = inventory.mac
    return _Probe(
        ip=ip,
        result=result,
        hostname=hostname,
        mac=mac,
        serial_number=serial_number,
    )


def _save_online_probe(probe: _Probe, sector_id: int) -> bool:
    """Найти машину по serial → hostname → IP и записать онлайн + историю."""
    device = _resolve_device(
        ip=probe.ip,
        hostname=probe.hostname,
        serial_number=probe.serial_number,
        sector_id=sector_id,
    )
    device.ip = probe.ip
    device.sector_id = sector_id
    device.last_status = DeviceStatus.ONLINE
    device.last_response_time_ms = probe.result.response_time_ms
    device.last_seen = utcnow()
    if probe.hostname:
        device.hostname = probe.hostname
    if probe.mac:
        device.mac = probe.mac
    if probe.serial_number:
        device.serial_number = probe.serial_number

    try:
        # Сначала id новой строки, потом история с внешним ключом.
        db.session.flush()
        db.session.add(
            DeviceHistory(
                device_id=device.id,
                timestamp=utcnow(),
                status=DeviceStatus.ONLINE,
                response_time_ms=probe.result.response_time_ms,
            )
        )
        db.session.commit()
    except Exception:
        logger.exception("Не удалось сохранить онлайн-опрос %s", probe.ip)
        db.session.rollback()
        return False
    return True


def _save_offline_probe(probe: _Probe) -> bool | None:
    """Офлайн пишем только если машина уже известна по этому IP.

    True — обновили хотя бы одну строку; False — пустой адрес, ничего не создаём;
    None — ошибка COMMIT.
    """
    devices = db.session.scalars(select(Device).where(Device.ip == probe.ip)).all()
    if not devices:
        return False
    now = utcnow()
    for device in devices:
        device.last_status = DeviceStatus.OFFLINE
        device.last_response_time_ms = None
        db.session.add(
            DeviceHistory(
                device_id=device.id,
                timestamp=now,
                status=DeviceStatus.OFFLINE,
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


def _resolve_device(
    *,
    ip: str,
    hostname: str | None,
    serial_number: str | None,
    sector_id: int,
) -> Device:
    """Ключ идентичности: serial, иначе hostname, иначе текущий IP.

    При совпадении по serial/hostname IP и сектор обновляются у той же
    строки — история опросов не рвётся при смене адреса.
    """
    if serial_number:
        found = db.session.scalar(
            select(Device).where(Device.serial_number == serial_number)
        )
        if found is not None:
            return found

    if hostname:
        found = _find_by_hostname(hostname)
        if found is not None:
            # Если у найденной по имени уже другой serial — это другая машина
            # с тем же DNS-именем (редко). Тогда не склеиваем.
            if not found.serial_number or not serial_number:
                return found
            if found.serial_number == serial_number:
                return found

    found = db.session.scalar(select(Device).where(Device.ip == ip))
    if found is not None:
        # IP занял кто-то без своего serial/hostname — переиспользуем строку.
        # Если у строки уже есть чужой serial, а у зонда тоже другой/пустой —
        # всё равно обновляем эту «точку» только когда зонд без идентичности
        # или serial совпал; иначе создаём новую и оставляем старую офлайн.
        if serial_number and found.serial_number and found.serial_number != serial_number:
            pass
        elif hostname and found.hostname and not discovery_service.hostnames_match(
            found.hostname, hostname
        ) and (found.serial_number or serial_number):
            pass
        else:
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


def _find_by_hostname(hostname: str) -> Device | None:
    """Точное имя или то же короткое имя без DNS-суффикса."""
    key = discovery_service.hostname_key(hostname)
    if not key:
        return None
    # Сначала полное совпадение без регистра.
    found = db.session.scalar(
        select(Device).where(func.lower(Device.hostname) == hostname.strip().rstrip(".").lower())
    )
    if found is not None:
        return found
    # Короткое имя и варианты с суффиксом: n14002, n14002.example.com
    candidates = db.session.scalars(
        select(Device).where(
            Device.hostname.is_not(None),
            or_(
                func.lower(Device.hostname) == key,
                Device.hostname.ilike(f"{key}.%"),
            ),
        )
    ).all()
    for device in candidates:
        if discovery_service.hostnames_match(device.hostname, hostname):
            return device
    return None


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
