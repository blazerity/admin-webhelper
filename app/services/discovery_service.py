"""Имя хоста, MAC и серийный номер без сканера пакетов.

Четыре разные проверки, их легко перепутать:

* Имя (lookup_hostname) — вопрос к DNS: «какой PTR у этого IP?».
  Сервер и устройство могут быть в разных подсетях, DNS всё равно ответит,
  если запись заведена.
* MAC из ARP (lookup_mac) — адрес канального уровня. Его видно только когда
  этот сервер и устройство сидят в одном L2-сегменте: коммутатор
  доставил кадр, и ядро записало соседа в таблицу ARP (Windows)
  или neighbor (Linux). Через маршрутизатор MAC чужой сети не приходит.
* Серийник и MAC по WMI (lookup_wmi_inventory) — один DCOM-заход после ping:
  Win32_BIOS.SerialNumber и MAC адаптера с нашим IP
  (Win32_NetworkAdapterConfiguration). Нужна учётка DISCOVERY_* в окружении.
  Без неё возвращаем пустой инвентарь, идентичность строится по hostname,
  а MAC остаётся только из ARP.

Scapy здесь не используем. Ему нужен захват пакетов и дополнительные
права (raw socket / Npcap). Для первой версии достаточно прочитать
таблицу, которую ядро уже ведёт само. Пинг перед этим как раз заполняет
строку ARP, если хост ответил по ICMP.

В subprocess адрес попадает только после assert_public_ipv4.
shell=True нет: иначе «10.0.0.5; whoami» стало бы второй командой.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass

from flask import current_app

from app.services.net_utils import assert_public_ipv4, normalize_mac

logger = logging.getLogger(__name__)

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
# не держал весь опрос сектора. Два WQL в одной сессии укладываются сюда же.
_WMI_TIMEOUT_S = 8

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


def lookup_hostname(ip: str) -> str | None:
    """Обратный DNS. None — записи нет или сервер имён не ответил.

    socket.gethostbyaddr смотрит на тайм-аут по умолчанию всего процесса,
    отдельного аргумента у него нет. Поэтому на время вызова ставим 1 секунду
    и в finally возвращаем прежнее значение: иначе остальные сокеты приложения
    навсегда останутся с нашим тайм-аутом.
    """
    ip = assert_public_ipv4(ip)
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(1.0)
    try:
        hostname, _aliases, _addresses = socket.gethostbyaddr(ip)
    except OSError:
        return None
    finally:
        socket.setdefaulttimeout(previous)
    if not hostname:
        return None
    # Некоторые резолверы отдают FQDN с точкой на конце: "pc.example."
    hostname = hostname.strip().rstrip(".")
    if not hostname:
        return None
    return hostname[:255]


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


def lookup_wmi_inventory(ip: str) -> WmiInventory:
    """Серийник и MAC одним WMI-заходом после успешного ping.

    Без DISCOVERY_USERNAME/DISCOVERY_PASSWORD сразу пустой инвентарь —
    опрос не обязан ходить в WMI; идентичность тогда по hostname,
    MAC — только из ARP, если сегмент общий.
    """
    ip = assert_public_ipv4(ip)
    creds = discovery_credentials()
    if creds is None:
        return WmiInventory()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_wmi_inventory, ip, creds)
        try:
            return future.result(timeout=_WMI_TIMEOUT_S)
        except FuturesTimeout:
            logger.info("WMI inventory: таймаут для %s", ip)
            return WmiInventory()
        except Exception:
            logger.exception("WMI inventory: сбой для %s", ip)
            return WmiInventory()


def lookup_serial(ip: str) -> str | None:
    """Только серийник. Для полного опроса предпочтительнее lookup_wmi_inventory."""
    return lookup_wmi_inventory(ip).serial_number


def discovery_credentials() -> DiscoveryCredentials | None:
    """Учётка для WMI из окружения приложения. Пустые поля — WMI выключен."""
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


def _wmi_inventory(ip: str, creds: DiscoveryCredentials) -> WmiInventory:
    """Один DCOM к машине: BIOS serial + MAC адаптера с этим IP.

    Импорт Impacket — внутри, чтобы тесты без сети и без пакета
    не падали на загрузке модуля.
    """
    try:
        from impacket.dcerpc.v5.dcom import wmi
        from impacket.dcerpc.v5.dcomrt import DCOMConnection
        from impacket.dcerpc.v5.dtypes import NULL
    except ImportError:
        logger.warning("WMI inventory: пакет для удалённого WMI не установлен")
        return WmiInventory()

    dcom = None
    services = None
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
        serial_raw = _wmi_query_serial(services)
        mac = _wmi_query_mac(services, ip)
        return WmiInventory(
            serial_number=normalize_serial(serial_raw),
            mac=mac,
        )
    except Exception as exc:
        logger.info("WMI inventory: %s — %s", ip, exc)
        return WmiInventory()
    finally:
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
