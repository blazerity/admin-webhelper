"""Имя хоста, MAC и серийный номер без сканера пакетов.

Четыре разные проверки, их легко перепутать:

* Имя из DNS (lookup_hostname) — вопрос к DNS: «какой PTR у этого IP?».
  Сервер и устройство могут быть в разных подсетях, DNS всё равно ответит,
  если запись заведена. Без PTR (socket.herror Unknown host) имени нет.
* Имя из WMI — Win32_ComputerSystem.DNSHostName / Name в том же DCOM,
  что серийник и УЗ. Это имя машины, его пишем в devices.hostname.
  PTR — запасной, только если WMI имя не отдал.
* MAC из ARP (lookup_mac) — адрес канального уровня. Его видно только когда
  этот сервер и устройство сидят в одном L2-сегменте: коммутатор
  доставил кадр, и ядро записало соседа в таблицу ARP (Windows)
  или neighbor (Linux). Через маршрутизатор MAC чужой сети не приходит.
* Серийник, MAC, имя и текущая УЗ по WMI (lookup_wmi_inventory) — один DCOM
  после ping: Win32_BIOS.SerialNumber, MAC адаптера с нашим IP
  (Win32_NetworkAdapterConfiguration) и Win32_ComputerSystem
  (Name / DNSHostName / UserName). Учётка: сначала из Параметров
  (шифротекст в app_settings), иначе DISCOVERY_* в .env. Без неё
  возвращаем пустой инвентарь; идентичность без серийника — только
  заглушка по текущему IP, не по PTR-имени. MAC — только из ARP.
* Служба badagent и device_id (lookup_badagent) — для связи с bAD.
  Если службы нет, реестр и конфиг не читаем. Из config.json агента
  берём только поле device_id и сразу отбрасываем остальное: рядом лежит
  ключ агента. Процессор, память, диски и версию Windows WMI не читает:
  их приносит lookup bAD по кнопке на карточке.

Scapy здесь не используем. Ему нужен захват пакетов и дополнительные
права (raw socket / Npcap). Для первой версии достаточно прочитать
таблицу, которую ядро уже ведёт само. Пинг перед этим как раз заполняет
строку ARP, если хост ответил по ICMP.

В subprocess адрес попадает только после assert_public_ipv4.
shell=True нет: иначе «10.0.0.5; whoami» стало бы второй командой.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass

from flask import current_app

from app.services.net_utils import assert_public_ipv4, normalize_mac

logger = logging.getLogger(__name__)

# Сокеты DNS делят process-wide default timeout — сериализуем смену значения.
_dns_timeout_lock = threading.Lock()

# Две привычные записи MAC: aa:bb:cc:dd:ee:ff / aa-bb-cc-dd-ee-ff
# и cisco-вид aabb.ccdd.eeff. Дальше normalize_mac приводит к одному формату.
_MAC_RE = re.compile(
    r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}"
    r"|"
    r"(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}"
)

# Таблица соседей локальная, ответ почти мгновенный.
# Таймаут — предохранитель, если arp/ip зависнет.
_NEIGHBOR_TIMEOUT_S = 5

# WMI по DCOM на живой машине обычно < 2 с; потолок — чтобы один хост
# не держал весь опрос сектора. Три WQL в одной сессии укладываются сюда же.
_WMI_TIMEOUT_S = 10

_HKLM = 0x80000002
_MACHINE_GUID_KEY = r"SOFTWARE\Microsoft\Cryptography"
_BAD_AGENT_SERVICE = "badagent"
_AGENT_CONFIG_TAIL = r"blazerity\bAD\config.json"
_MAX_AGENT_CONFIG_BYTES = 65_536

# Заглушки BIOS/OEM, которые нельзя считать service tag.
_INVALID_SERIALS = {
    "",
    "0",
    "NONE",
    "N/A",
    "NA",
    "NULL",
    "TO BE FILLED BY O.E.M.",
    "TO BE FILLED BY OEM",
    "DEFAULT STRING",
    "SYSTEM SERIAL NUMBER",
    "SYSTEM SERIAL#",
    "CHASSIS SERIAL NUMBER",
    "123456789",
    "XXXXXXXXXX",
    "NOT SPECIFIED",
    "NOT AVAILABLE",
}


@dataclass(frozen=True)
class DiscoveryCredentials:
    username: str
    password: str
    domain: str


@dataclass(frozen=True)
class WmiInventory:
    """Что удалось прочитать по WMI за один заход к машине."""

    serial_number: str | None = None
    mac: str | None = None
    # Win32_ComputerSystem.DNSHostName или Name, если DNS-имя пустое.
    hostname: str | None = None
    # Win32_ComputerSystem.UserName: DOMAIN\user или пусто.
    logged_on_user: str | None = None


@dataclass(frozen=True)
class BadAgentProbe:
    """Служба badagent и MachineGuid. Железо сюда не входит.

    machine_guid живёт только в памяти вызова lookup. На карточку его не пишем.
    """

    service_present: bool = False
    # Идентификатор для lookup. В базу не пишется. Это device_id из конфига
    # агента либо, если файл не открылся, MachineGuid из реестра.
    machine_guid: str | None = None
    wmi_failed: bool = False
    # config | registry | пусто.
    id_source: str = ""
    # Короткая причина без текста исключения, учётки и ключа агента.
    detail: str = ""



def lookup_hostname(ip: str) -> str | None:
    """Обратный DNS. None — записи нет или сервер имён не ответил.

    socket.gethostbyaddr смотрит на тайм-аут по умолчанию всего процесса,
    отдельного аргумента у него нет. Поэтому на время вызова ставим 1 секунду
    и в finally возвращаем прежнее значение: иначе остальные сокеты приложения
    навсегда останутся с нашим тайм-аутом. Под lock — опрос идёт из пула
    потоков, и гонка setdefaulttimeout ломала бы DNS у соседних воркеров.
    """
    ip = assert_public_ipv4(ip)
    with _dns_timeout_lock:
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(1.0)
        try:
            hostname, _aliases, _addresses = socket.gethostbyaddr(ip)
        except OSError as exc:
            logger.debug("PTR lookup: %s — %s", ip, exc)
            return None
        finally:
            socket.setdefaulttimeout(previous)
    return _normalize_hostname(hostname)


def lookup_mac(ip: str) -> str | None:
    """MAC из таблицы соседей ядра, не из сети «вообще».

    MAC виден только если этот сервер в одном L2-сегменте с хостом
    (таблица ARP на Windows, neighbor на Linux). Через маршрутизатор
    адрес канального уровня чужой подсети сюда не попадает.

    Scapy не используем: захват пакетов требует отдельных привилегий
    и драйвера. Чтения таблицы ядра для первой версии достаточно.
    Неизвестный адрес — None, это нормально (другая подсеть или хост молчит).
    """
    ip = assert_public_ipv4(ip)
    if os.name == "nt":
        return _mac_from_command(["arp", "-a", ip], ip)
    # Linux: сначала iproute2. Если утилиты нет или строки с MAC нет —
    # старый arp из net-tools.
    found = _mac_from_command(["ip", "neigh", "show", ip], ip)
    if found:
        return found
    return _mac_from_command(["arp", "-a", ip], ip)


def _wmi_timed_call(worker, ip: str, creds: DiscoveryCredentials, timeout_s: float, empty):
    """Один поток + таймаут вокруг DCOM. Пустой результат при таймауте/сбое."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker, ip, creds)
        try:
            return future.result(timeout=timeout_s)
        except FuturesTimeout:
            logger.debug("WMI: таймаут для %s", ip)
            return empty
        except Exception:
            logger.exception("WMI: сбой для %s", ip)
            return empty


def lookup_wmi_inventory(
    ip: str,
    creds: DiscoveryCredentials | None = None,
) -> WmiInventory:
    """Серийник, имя, MAC и УЗ одним WMI-заходом после успешного ping.

    creds лучше передать с главного потока опроса: в ThreadPoolExecutor
    нет Flask app context, и читать app_settings/current_app оттуда нельзя.
    Без учётки — пустой инвентарь; без серийника устройство не склеиваем
    по имени, только по текущему IP с пустым SN. MAC — из ARP, если сегмент общий.
    """
    ip = assert_public_ipv4(ip)
    if creds is None:
        creds = discovery_credentials()
    if creds is None:
        return WmiInventory()
    return _wmi_timed_call(
        _wmi_inventory, ip, creds, _WMI_TIMEOUT_S, WmiInventory()
    )


def lookup_serial(ip: str) -> str | None:
    """Только серийник. Для полного опроса предпочтительнее lookup_wmi_inventory."""
    return lookup_wmi_inventory(ip).serial_number


def lookup_badagent(
    ip: str,
    creds: DiscoveryCredentials | None = None,
) -> BadAgentProbe:
    """Проверить службу badagent и, если она есть, прочитать device_id.

    Сначала поле device_id из ``%ProgramData%\\blazerity\\bAD\\config.json``:
    на сервере bAD записан именно он. Остальное содержимое файла, включая
    ключ агента, отбрасывается и в журнал не попадает. Если файл не открылся,
    запасной источник — MachineGuid в реестре. Без службы ни реестр, ни файл
    не открываем.
    """
    ip = assert_public_ipv4(ip)
    if creds is None:
        creds = discovery_credentials()
    if creds is None:
        return BadAgentProbe(wmi_failed=True)
    return _wmi_timed_call(
        _read_badagent, ip, creds, _WMI_TIMEOUT_S, BadAgentProbe(wmi_failed=True)
    )



def discovery_credentials() -> DiscoveryCredentials | None:
    """Учётка для WMI: БД (Параметры) важнее .env. Пустые поля — WMI выключен.

    Вызывать из потока с app.app_context() (главный поток poll / flask CLI),
    не из рабочих потоков ThreadPoolExecutor.
    """
    from app.services.crypto_service import CryptoError, CryptoNotConfigured
    from app.services.settings_service import get_stored_discovery_credentials

    try:
        stored = get_stored_discovery_credentials()
    except (CryptoNotConfigured, CryptoError) as exc:
        logger.warning("discovery credentials: не удалось прочитать из БД — %s", exc)
        stored = None
    except RuntimeError:
        # Нет Flask app context — только .env / environ.
        logger.debug(
            "discovery credentials: нет app context, читаем DISCOVERY_* из environ"
        )
        stored = None
    if stored is not None:
        return DiscoveryCredentials(
            username=stored.username,
            password=stored.password,
            domain=stored.domain,
        )

    try:
        username = str(current_app.config.get("DISCOVERY_USERNAME") or "").strip()
        password = str(current_app.config.get("DISCOVERY_PASSWORD") or "")
        domain = str(current_app.config.get("DISCOVERY_DOMAIN") or "").strip()
    except RuntimeError:
        username = (os.environ.get("DISCOVERY_USERNAME") or "").strip()
        password = os.environ.get("DISCOVERY_PASSWORD") or ""
        domain = (os.environ.get("DISCOVERY_DOMAIN") or "").strip()
    if not username or not password:
        return None
    return DiscoveryCredentials(username=username, password=password, domain=domain)


def normalize_serial(value: str | None) -> str | None:
    """Верхний регистр, обрезка мусора BIOS. None — не годится как ключ."""
    if not value:
        return None
    cleaned = " ".join(str(value).split()).strip().upper()
    if not cleaned or cleaned in _INVALID_SERIALS:
        return None
    if len(cleaned) > 64:
        cleaned = cleaned[:64]
    # Слишком короткие «серийники» обычно заглушки.
    if len(cleaned) < 3:
        return None
    return cleaned


def hostname_key(value: str | None) -> str | None:
    """Короткое имя без домена, lower-case: n14002.example.com → n14002."""
    if not value:
        return None
    cleaned = value.strip().rstrip(".").lower()
    if not cleaned:
        return None
    return cleaned.split(".", 1)[0][:255] or None


def hostnames_match(left: str | None, right: str | None) -> bool:
    """Сравнение имён без учёта регистра и DNS-суффикса."""
    a = hostname_key(left)
    b = hostname_key(right)
    return bool(a and b and a == b)


def preferred_hostname(
    *,
    os_hostname: str | None = None,
    ptr: str | None = None,
) -> str | None:
    """Имя для карточки: WMI/OS важнее PTR.

    PTR бывает общим на несколько адресов (устаревший reverse DNS).
    Win32_ComputerSystem — фактическое имя компьютера.
    """
    return os_hostname or ptr


def _wmi_inventory(ip: str, creds: DiscoveryCredentials) -> WmiInventory:
    """Один DCOM к машине: BIOS serial, MAC, имя и текущая УЗ.

    Импорт Impacket — внутри, чтобы тесты без сети и без пакета
    не падали на загрузке модуля.
    """
    dcom, services = _wmi_open(ip, creds)
    if services is None:
        return WmiInventory()
    try:
        serial_raw = _wmi_query_serial(services)
        mac = _wmi_query_mac(services, ip)
        # После успешного WMI UserName всегда строка: "" = никто не залогинен.
        # None оставляем только когда WMI не вызывали / сессия упала.
        cs_hostname, logged_raw = _wmi_query_computer_system(services)
        return WmiInventory(
            serial_number=normalize_serial(serial_raw),
            mac=mac,
            hostname=cs_hostname,
            logged_on_user=logged_raw if logged_raw is not None else "",
        )
    except Exception as exc:
        logger.debug("WMI inventory: %s — %s", ip, exc)
        return WmiInventory()
    finally:
        _wmi_close(dcom, services)



def _read_badagent(ip: str, creds: DiscoveryCredentials) -> BadAgentProbe:
    """Один DCOM: служба badagent, затем device_id. Железо не читаем."""
    try:
        import impacket.dcerpc.v5.dcom.wmi  # noqa: F401
    except ImportError:
        return BadAgentProbe(wmi_failed=True, detail="нет пакета удалённого WMI")
    dcom, services = _wmi_open(ip, creds)
    if services is None:
        return BadAgentProbe(wmi_failed=True, detail="DCOM не открылся")
    try:
        if not _wmi_service_present(services, _BAD_AGENT_SERVICE):
            return BadAgentProbe(service_present=False)
        program_data = _query_program_data(services)
        config_id, config_note = _read_config_device_id(ip, creds, program_data)
        if config_id:
            return BadAgentProbe(
                service_present=True,
                machine_guid=config_id,
                id_source="config",
            )
        guid = _read_machine_guid(dcom)
        if guid:
            return BadAgentProbe(
                service_present=True,
                machine_guid=guid,
                id_source="registry",
                detail=config_note,
            )
        return BadAgentProbe(
            service_present=True,
            detail=(
                "MachineGuid в реестре пустой или закрыт для этой учётки: "
                "удалённо его читает только администратор. "
                + config_note
            ),
        )
    except Exception as exc:
        logger.debug("badagent: %s — %s", ip, type(exc).__name__)
        return BadAgentProbe(wmi_failed=True, detail="запрос службы упал")
    finally:
        _wmi_close(dcom, services)


def _query_program_data(services) -> str:
    """Системный ProgramData. Пусто — дальше берём C:\\ProgramData."""
    enum_obj = None
    try:
        enum_obj = services.ExecQuery(
            "SELECT VariableValue FROM Win32_Environment "
            "WHERE Name = 'ProgramData' AND SystemVariable = True"
        )
        for props in _wmi_enum_properties(enum_obj):
            text = _wmi_prop_text(props, "VariableValue")
            if text:
                return text
    except Exception as exc:
        logger.debug("ProgramData: %s", type(exc).__name__)
    finally:
        _wmi_release(enum_obj)
    return ""


def _read_config_device_id(
    ip: str,
    creds: DiscoveryCredentials,
    program_data: str,
) -> tuple[str | None, str]:
    """Прочитать только device_id. Тело файла в журнал и в возврат не входит."""
    share, relative = _agent_config_location(program_data)
    try:
        raw = _smb_read_file(ip, creds, share, relative)
    except Exception as exc:
        logger.debug("badagent config: %s — %s", ip, type(exc).__name__)
        return None, (
            "Конфиг агента не открылся. Его тоже читает только администратор, "
            f"по пути \\\\{share}\\{relative}."
        )
    if raw is None:
        return None, (
            "Конфиг агента слишком большой, целиком его не разбирали."
        )
    device_id = device_id_from_config_bytes(raw)
    del raw
    if not device_id:
        return None, "Конфиг агента открылся, поля device_id в нём нет."
    return device_id, ""


def _agent_config_location(program_data: str) -> tuple[str, str]:
    """(шара, путь внутри шары) для config.json. Чужой ``..`` не принимаем."""
    text = (program_data or r"C:\ProgramData").strip().replace("/", "\\").rstrip("\\")
    share = "C$"
    folder = "ProgramData"
    if len(text) >= 3 and text[1] == ":" and text[0].isalpha():
        share = text[0].upper() + "$"
        folder = text[3:].lstrip("\\") or "ProgramData"
    parts = [part for part in folder.split("\\") if part and part != "."]
    if any(part == ".." for part in parts):
        share, parts = "C$", ["ProgramData"]
    relative = "\\".join([*parts, _AGENT_CONFIG_TAIL])
    return share, relative


def device_id_from_config_bytes(raw: bytes) -> str | None:
    """Достать device_id и забыть остальной JSON, включая api_key."""
    if not raw or len(raw) > _MAX_AGENT_CONFIG_BYTES:
        return None
    text = _decode_config_text(raw)
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    finally:
        del text
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("device_id")
    parsed.clear()
    if not isinstance(value, str):
        return None
    return _clean_device_id(value)


def _decode_config_text(raw: bytes) -> str | None:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeError:
            return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError:
        return None


def _clean_device_id(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or len(text) > 128:
        return None
    if any(ord(ch) < 32 for ch in text):
        return None
    lowered = text.lower()
    if lowered.startswith(("bad_ak_", "bad_in_", "bad_ps_", "bearer ")):
        return None
    return text


def _smb_read_file(
    ip: str,
    creds: DiscoveryCredentials,
    share: str,
    relative: str,
) -> bytes | None:
    """Прочитать файл по SMB. None — файл больше допустимого размера."""
    from impacket.smbconnection import SMBConnection

    conn = SMBConnection(ip, ip, sess_port=445, timeout=5)
    try:
        conn.login(creds.username, creds.password, creds.domain or "")
        sink = _LimitedSink(_MAX_AGENT_CONFIG_BYTES)
        conn.getFile(share, relative, sink.write)
        if sink.overflow:
            return None
        return sink.getvalue()
    finally:
        try:
            conn.close()
        except Exception:
            pass


class _LimitedSink(io.BytesIO):
    """Останавливает чтение, если конфиг раздули."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.overflow = False

    def write(self, data) -> int:  # noqa: ANN001
        if self.overflow or self.tell() + len(data) > self.limit:
            self.overflow = True
            return len(data)
        return super().write(data)


def _wmi_service_present(services, name: str) -> bool:
    """Служба установлена. Состояние (работает / остановлена) не важно."""
    safe = name.replace("'", "")
    enum_obj = services.ExecQuery(
        f"SELECT Name FROM Win32_Service WHERE Name = '{safe}'"
    )
    try:
        for props in _wmi_enum_properties(enum_obj):
            found = _wmi_prop_text(props, "Name")
            if found and found.lower() == name.lower():
                return True
    finally:
        _wmi_release(enum_obj)
    return False


def _read_machine_guid(dcom) -> str | None:
    """HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid. Только чтение."""
    values = _wmi_reg_strings(dcom, _MACHINE_GUID_KEY, ("MachineGuid",))
    return _clean_machine_guid(values[0] if values else None)


def _clean_machine_guid(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or len(text) > 128:
        return None
    if any(ord(ch) < 32 for ch in text):
        return None
    return text


def _wmi_open(ip: str, creds: DiscoveryCredentials):
    """(dcom, services) или (None, None), если Impacket нет / DCOM не открылся."""
    try:
        from impacket.dcerpc.v5.dcom import wmi
        from impacket.dcerpc.v5.dcomrt import DCOMConnection
        from impacket.dcerpc.v5.dtypes import NULL
    except ImportError:
        logger.warning("WMI: пакет для удалённого WMI не установлен")
        return None, None

    dcom = None
    try:
        dcom = DCOMConnection(
            ip,
            creds.username,
            creds.password,
            creds.domain,
            "",
            "",
            None,
            oxidResolver=True,
            doKerberos=False,
        )
        interface = dcom.CoCreateInstanceEx(wmi.CLSID_WbemLevel1Login, wmi.IID_IWbemLevel1Login)
        login = wmi.IWbemLevel1Login(interface)
        services = login.NTLMLogin("//./root/cimv2", NULL, NULL)
        login.RemRelease()
        return dcom, services
    except Exception as exc:
        logger.debug("WMI open: %s — %s", ip, exc)
        if dcom is not None:
            try:
                dcom.disconnect()
            except Exception:
                pass
        return None, None


def _wmi_close(dcom, services) -> None:
    if services is not None:
        try:
            services.RemRelease()
        except Exception:
            pass
    if dcom is not None:
        try:
            dcom.disconnect()
        except Exception:
            pass


def _wmi_query_serial(services) -> str | None:
    """SELECT SerialNumber FROM Win32_BIOS — первый непустой ответ."""
    enum_obj = services.ExecQuery("SELECT SerialNumber FROM Win32_BIOS")
    try:
        for props in _wmi_enum_properties(enum_obj):
            raw = props.get("SerialNumber", {}).get("value")
            if raw is not None and str(raw).strip():
                return str(raw)
    finally:
        try:
            enum_obj.RemRelease()
        except Exception:
            pass
    return None


def _wmi_query_computer_system(services) -> tuple[str | None, str | None]:
    """Имя машины и интерактивная УЗ из Win32_ComputerSystem.

    Один WQL: DNSHostName / Name и UserName. user=None — в строке нет
    консольной сессии; вызывающий (_wmi_inventory) превращает это в "".
    """
    enum_obj = services.ExecQuery(
        "SELECT Name, DNSHostName, Domain, UserName FROM Win32_ComputerSystem"
    )
    hostname = None
    user = None
    try:
        for props in _wmi_enum_properties(enum_obj):
            if hostname is None:
                hostname = _hostname_from_wmi_props(props)
            if user is None:
                text = _wmi_prop_text(props, "UserName")
                if text:
                    user = text[:255]
            if hostname and user:
                break
    finally:
        try:
            enum_obj.RemRelease()
        except Exception:
            pass
    return hostname, user


def _hostname_from_wmi_props(props: dict) -> str | None:
    """DNSHostName, иначе Name. К короткому DNS-имени дописываем Domain."""
    dns = _normalize_hostname(_wmi_prop_text(props, "DNSHostName"))
    short = _normalize_hostname(_wmi_prop_text(props, "Name"))
    domain = (_wmi_prop_text(props, "Domain") or "").strip().rstrip(".")
    if dns:
        if "." in dns:
            return dns
        if domain and "." in domain:
            return f"{dns}.{domain}"[:255]
        return dns
    return short


def _wmi_prop_text(props: dict, key: str) -> str | None:
    raw = props.get(key, {}).get("value")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _normalize_hostname(value: str | None) -> str | None:
    """Обрезка пробелов и завершающей точки DNS. None — пустое имя."""
    if not value:
        return None
    hostname = value.strip().rstrip(".")
    if not hostname:
        return None
    return hostname[:255]


def _wmi_query_mac(services, ip: str) -> str | None:
    """MAC адаптера, у которого в IPAddress есть наш адрес.

    Если точного совпадения нет — берём первый валидный MAC среди
    IPEnabled-адаптеров (лучше хоть какой-то, чем пусто через маршрутизатор).
    """
    enum_obj = services.ExecQuery(
        "SELECT MACAddress, IPAddress FROM Win32_NetworkAdapterConfiguration "
        "WHERE IPEnabled = True"
    )
    fallback: str | None = None
    try:
        for props in _wmi_enum_properties(enum_obj):
            mac = normalize_mac(str(props.get("MACAddress", {}).get("value") or ""))
            if not mac:
                continue
            if _wmi_ip_list_contains(props.get("IPAddress", {}).get("value"), ip):
                return mac
            if fallback is None:
                fallback = mac
    finally:
        try:
            enum_obj.RemRelease()
        except Exception:
            pass
    return fallback



def _wmi_namespace_services(dcom, namespace: str):
    """Доп. IWbemServices на уже открытом DCOM (например root/default для реестра)."""
    from impacket.dcerpc.v5.dcom import wmi
    from impacket.dcerpc.v5.dtypes import NULL

    interface = dcom.CoCreateInstanceEx(wmi.CLSID_WbemLevel1Login, wmi.IID_IWbemLevel1Login)
    login = wmi.IWbemLevel1Login(interface)
    services = login.NTLMLogin(f"//./{namespace}", NULL, NULL)
    login.RemRelease()
    return services


def _wmi_reg_result_text(result) -> str | None:
    """sValue из ответа Impacket GetStringValue."""
    if result is None:
        return None
    direct = getattr(result, "sValue", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    props = result.getProperties() if hasattr(result, "getProperties") else None
    if props:
        return _wmi_prop_text(props, "sValue")
    return None


def _wmi_reg_strings(
    dcom, subkey: str, value_names: tuple[str, ...]
) -> tuple[str | None, ...]:
    """Несколько HKLM-строк одним StdRegProv (root/default + SpawnInstance)."""
    services = None
    class_obj = None
    instance = None
    values: list[str | None] = [None] * len(value_names)
    try:
        services = _wmi_namespace_services(dcom, "root/default")
        class_obj, _ = services.GetObject("StdRegProv")
        instance = class_obj.SpawnInstance()
        getter = getattr(instance, "GetStringValue", None)
        if getter is None:
            return tuple(values)
        for index, value_name in enumerate(value_names):
            result = None
            try:
                result = getter(_HKLM, subkey, value_name)
                values[index] = _wmi_reg_result_text(result)
            except Exception as exc:
                logger.debug("WMI registry %s\\%s: %s", subkey, value_name, exc)
            finally:
                _wmi_release(result)
        return tuple(values)
    except Exception as exc:
        logger.debug("WMI registry open %s: %s", subkey, exc)
        return tuple(values)
    finally:
        _wmi_release(instance)
        _wmi_release(class_obj)
        if services is not None:
            try:
                services.RemRelease()
            except Exception:
                pass




def _wmi_release(obj) -> None:
    if obj is None:
        return
    try:
        obj.RemRelease()
    except Exception:
        pass


def _wmi_enum_properties(enum_obj):
    """Итератор getProperties() по результатам ExecQuery."""
    while True:
        try:
            item = enum_obj.Next(0xFFFFFFFF, 1)[0]
        except Exception as exc:
            if "S_FALSE" in str(exc):
                break
            raise
        yield item.getProperties()


def _wmi_ip_list_contains(raw_addresses, ip: str) -> bool:
    """IPAddress в WMI — список/кортеж строк или одно значение."""
    if raw_addresses is None:
        return False
    if isinstance(raw_addresses, (list, tuple)):
        values = raw_addresses
    else:
        values = [raw_addresses]
    return any(str(item).strip() == ip for item in values if item is not None)


def _mac_from_command(argv: list[str], ip: str) -> str | None:
    text = _command_output(argv)
    if not text:
        return None
    return _mac_in_text(text, ip)


def _command_output(argv: list[str]) -> str | None:
    """Текст команды или None, если её не удалось запустить.

    None значит «пробуем следующий способ» (на Linux — arp после ip).
    Ненулевой код всё равно отдаём разбирать: иногда MAC уже есть в stdout.
    """
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_NEIGHBOR_TIMEOUT_S,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return f"{completed.stdout or ''}\n{completed.stderr or ''}"


def _mac_in_text(text: str, ip: str) -> str | None:
    """Ищет MAC на строке, где упомянут именно этот IP.

    Граница по цифрам нужна, чтобы 10.0.0.5 не совпал с 10.0.0.50.
    """
    ip_token = re.compile(rf"(?<!\d){re.escape(ip)}(?!\d)")
    for line in text.splitlines():
        if ip_token.search(line) is None:
            continue
        for match in _MAC_RE.finditer(line):
            mac = normalize_mac(match.group(0))
            if mac:
                return mac
    return None
