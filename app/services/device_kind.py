"""Тип устройства по hostname.

Имена рабочих станций — буква + 3 или 5 цифр (n179, w11471, v100).
Остальное не считаем ноутбуком/СБ: ngfw-gk3 не должен стать N-ноутбуком.
"""

from __future__ import annotations

import re
from collections import Counter

KIND_NOTEBOOK = "notebook"
KIND_DESKTOP = "desktop"
KIND_VDS = "vds"
KIND_SERVER = "server"
KIND_FIREWALL = "firewall"
KIND_OTHER = "other"

KIND_ORDER = (
    KIND_NOTEBOOK,
    KIND_DESKTOP,
    KIND_VDS,
    KIND_SERVER,
    KIND_FIREWALL,
    KIND_OTHER,
)

KIND_LABELS = {
    KIND_NOTEBOOK: "Ноутбук",
    KIND_DESKTOP: "Системный блок",
    KIND_VDS: "VDS",
    KIND_SERVER: "Сервер",
    KIND_FIREWALL: "Сетевое устройство",
    KIND_OTHER: "Устройство",
}

KIND_SHORT_LABELS = {
    KIND_NOTEBOOK: "Ноутбук",
    KIND_DESKTOP: "СБ",
    KIND_VDS: "VDS",
    KIND_SERVER: "Сервер",
    KIND_FIREWALL: "Firewall",
    KIND_OTHER: "Прочее",
}

_WORKSTATION_RE = re.compile(r"^([nwv])(?:\d{3}|\d{5})$")
_SERVER_PREFIXES = ("ktn", "spb", "kgl")
_FIREWALL_PREFIXES = ("ngfw", "ngwf")


def hostname_short(hostname: str | None) -> str:
    """Первая метка FQDN в нижнем регистре: W179.stepcon.ru → w179."""
    name = (hostname or "").strip().lower().rstrip(".")
    if not name:
        return ""
    return name.split(".", 1)[0]


def classify_device_kind(hostname: str | None) -> str:
    """notebook / desktop / vds / server / firewall / other."""
    short = hostname_short(hostname)
    if not short:
        return KIND_OTHER
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
    return KIND_OTHER


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
