"""Тип устройства и сортировка имён.

Имена рабочих станций — буква + 3 или 5 цифр (n179, w11471, v100).
Остальное не считаем ноутбуком/СБ: ngfw-gk3 не должен стать N-ноутбуком.

Windows vs периферия (камера / MikroTik / МФУ) — по тому, что уже есть
после опроса, без новых сетевых проб:

1. Свои префиксы AD (n/w/v, ktn/spb/kgl, ngfw) важнее всего.
2. Серийник с WMI значит «это Windows»: камерой/принтером не метим.
3. Иначе имя (PTR) и OUI MAC — камера, MikroTik, МФУ.

Дальше точнее: TCP 445/135 (Windows), 8728/8291 (MikroTik), 554 (камера),
9100/515 (печать), SNMP sysDescr. Это уже отдельный шаг опроса.
"""

from __future__ import annotations

import re
from collections import Counter

KIND_NOTEBOOK = "notebook"
KIND_DESKTOP = "desktop"
KIND_VDS = "vds"
KIND_SERVER = "server"
KIND_FIREWALL = "firewall"
KIND_ROUTER = "router"
KIND_CAMERA = "camera"
KIND_PRINTER = "printer"
KIND_OTHER = "other"

KIND_ORDER = (
    KIND_NOTEBOOK,
    KIND_DESKTOP,
    KIND_VDS,
    KIND_SERVER,
    KIND_FIREWALL,
    KIND_ROUTER,
    KIND_CAMERA,
    KIND_PRINTER,
    KIND_OTHER,
)

KIND_LABELS = {
    KIND_NOTEBOOK: "Ноутбук",
    KIND_DESKTOP: "Системный блок",
    KIND_VDS: "VDS",
    KIND_SERVER: "Сервер",
    KIND_FIREWALL: "Сетевое устройство",
    KIND_ROUTER: "MikroTik",
    KIND_CAMERA: "Камера",
    KIND_PRINTER: "МФУ",
    KIND_OTHER: "Устройство",
}

KIND_SHORT_LABELS = {
    KIND_NOTEBOOK: "Ноутбук",
    KIND_DESKTOP: "СБ",
    KIND_VDS: "VDS",
    KIND_SERVER: "Сервер",
    KIND_FIREWALL: "Firewall",
    KIND_ROUTER: "MikroTik",
    KIND_CAMERA: "Камера",
    KIND_PRINTER: "МФУ",
    KIND_OTHER: "Прочее",
}

_WORKSTATION_RE = re.compile(r"^([nwv])(?:\d{3}|\d{5})$")
_SERVER_PREFIXES = ("ktn", "spb", "kgl")
_FIREWALL_PREFIXES = ("ngfw", "ngwf")
_NUM_SPLIT_RE = re.compile(r"(\d+)")
_TOKEN_SPLIT_RE = re.compile(r"[.\-_\s]+")

# Токены имени: отдельная метка FQDN или явное вхождение.
_CAMERA_TOKENS = (
    "cam",
    "camera",
    "ipc",
    "ipcam",
    "cctv",
    "hikvision",
    "hik",
    "dahua",
    "axis",
    "ds-2cd",
    "dh-ipc",
)
_PRINTER_TOKENS = (
    "printer",
    "print",
    "mfp",
    "mfu",
    "xerox",
    "kyocera",
    "ricoh",
    "brother",
    "laserjet",
    "officejet",
    "deskjet",
    "canon",
)
_ROUTER_TOKENS = (
    "mikrotik",
    "routerboard",
    "router",
)
_MIKROTIK_MODEL_RE = re.compile(
    r"^(rb|crs|ccr|hex|hap|cap|ltap|wap)(\d|[.-]|$)",
    re.IGNORECASE,
)

# Первые 3 октета MAC (AA:BB:CC) — короткий справочник, не вся IEEE OUI.
_OUI_KIND = {
    "00:0C:42": KIND_ROUTER,
    "4C:5E:0C": KIND_ROUTER,
    "48:8F:5A": KIND_ROUTER,
    "E4:8D:8C": KIND_ROUTER,
    "6C:3B:6B": KIND_ROUTER,
    "DC:2C:6E": KIND_ROUTER,
    "18:FD:74": KIND_ROUTER,
    "2C:C8:1B": KIND_ROUTER,
    "C4:AD:34": KIND_ROUTER,
    "74:4D:28": KIND_ROUTER,
    "D4:CA:6D": KIND_ROUTER,
    "B8:69:F4": KIND_ROUTER,
    "08:55:31": KIND_ROUTER,
    "64:D1:54": KIND_ROUTER,
    "78:9A:18": KIND_ROUTER,
    "44:47:CC": KIND_CAMERA,
    "54:C4:15": KIND_CAMERA,
    "BC:AD:28": KIND_CAMERA,
    "C0:56:E3": KIND_CAMERA,
    "28:57:BE": KIND_CAMERA,
    "98:8B:0A": KIND_CAMERA,
    "3C:EF:8C": KIND_CAMERA,
    "90:02:A9": KIND_CAMERA,
    "14:A7:8B": KIND_CAMERA,
    "00:40:8C": KIND_CAMERA,
    "AC:CC:8E": KIND_CAMERA,
    "9C:8E:99": KIND_PRINTER,
    "F4:CE:46": KIND_PRINTER,
    "00:9C:02": KIND_PRINTER,
    "00:1E:0B": KIND_PRINTER,
    "18:60:24": KIND_PRINTER,
    "70:10:6F": KIND_PRINTER,
}


def hostname_short(hostname: str | None) -> str:
    """Первая метка FQDN в нижнем регистре: W179.stepcon.ru → w179."""
    name = (hostname or "").strip().lower().rstrip(".")
    if not name:
        return ""
    return name.split(".", 1)[0]


def hostname_sort_key(hostname: str | None, ip: str | None = None) -> tuple:
    """n100 < n101 < n1000 < n10000; без имени — в конец, по IP."""
    name = (hostname or "").strip().lower()
    ip_key = _ip_sort_parts(ip)
    if not name:
        return (1, (), ip_key)
    parts: list[tuple[int, int | str]] = []
    for chunk in _NUM_SPLIT_RE.split(name):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append((1, int(chunk)))
        else:
            parts.append((0, chunk))
    return (0, tuple(parts), ip_key)


def _ip_sort_parts(ip: str | None) -> tuple[int, int, int, int]:
    bits = (ip or "").strip().split(".")
    if len(bits) == 4:
        try:
            return (int(bits[0]), int(bits[1]), int(bits[2]), int(bits[3]))
        except ValueError:
            pass
    return (999, 999, 999, 999)


def classify_device_kind(
    hostname: str | None,
    *,
    serial_number: str | None = None,
    mac: str | None = None,
) -> str:
    """notebook / desktop / vds / server / firewall / router / camera / printer / other."""
    short = hostname_short(hostname)
    named = _kind_from_naming(short)
    if named:
        return named
    # WMI-серийник есть только у Windows: периферию по слабому MAC не ставим.
    if (serial_number or "").strip():
        return KIND_OTHER
    from_name = _kind_from_hostname_tokens(hostname)
    if from_name:
        return from_name
    from_mac = _kind_from_mac(mac)
    if from_mac:
        return from_mac
    return KIND_OTHER


def _kind_from_naming(short: str) -> str | None:
    if not short:
        return None
    if short.startswith(_FIREWALL_PREFIXES):
        return KIND_FIREWALL
    match = _WORKSTATION_RE.fullmatch(short)
    if match:
        letter = match.group(1)
        if letter == "n":
            return KIND_NOTEBOOK
        if letter == "w":
            return KIND_DESKTOP
        return KIND_VDS
    if short.startswith(_SERVER_PREFIXES):
        return KIND_SERVER
    return None


def _kind_from_hostname_tokens(hostname: str | None) -> str | None:
    name = (hostname or "").strip().lower().rstrip(".")
    if not name:
        return None
    short = hostname_short(name)
    if _MIKROTIK_MODEL_RE.match(short) or _has_token(name, _ROUTER_TOKENS):
        return KIND_ROUTER
    if _has_token(name, _CAMERA_TOKENS):
        return KIND_CAMERA
    if _has_token(name, _PRINTER_TOKENS):
        return KIND_PRINTER
    return None


def _has_token(name: str, tokens: tuple[str, ...]) -> bool:
    parts = {part for part in _TOKEN_SPLIT_RE.split(name) if part}
    compact = name.replace("-", "").replace("_", "").replace(".", "")
    for token in tokens:
        if token in parts:
            return True
        if "-" in token and token in name:
            return True
        if len(token) >= 4 and token in compact:
            return True
    return False


def _kind_from_mac(mac: str | None) -> str | None:
    raw = (mac or "").strip().upper().replace("-", ":")
    if len(raw) < 8:
        return None
    return _OUI_KIND.get(raw[:8])


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, KIND_LABELS[KIND_OTHER])


def kind_short_label(kind: str) -> str:
    return KIND_SHORT_LABELS.get(kind, KIND_SHORT_LABELS[KIND_OTHER])


def kind_counts(devices) -> list[tuple[str, str, int]]:
    """Ненулевые (kind, short_label, count) в стабильном порядке."""
    counts = Counter(getattr(device, "kind", KIND_OTHER) for device in devices)
    return [
        (kind, KIND_SHORT_LABELS[kind], counts[kind])
        for kind in KIND_ORDER
        if counts.get(kind)
    ]
