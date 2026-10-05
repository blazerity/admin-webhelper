"""Разбор снимка железа Windows: CPU, ОЗУ, диски, версия ОС.

Чистые функции без сети и без Flask: их зовут WMI-слой и тесты.
WMI отдаёт сырые Caption/байты; здесь — семейство 10/11/Server,
редакция Pro/Enterprise и DisplayVersion (25H2/26H2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_GIB = 1024 ** 3

_EDITION_ID = {
    "professional": "Pro",
    "professionaln": "Pro",
    "professionalworkstation": "Pro for Workstations",
    "enterprise": "Enterprise",
    "enterprisen": "Enterprise",
    "enterprises": "Enterprise",
    "enterpriseg": "Enterprise",
    "education": "Education",
    "core": "Home",
    "coresinglelanguage": "Home",
    "corecountryspecific": "Home",
    "serverstandard": "Standard",
    "serverdatacenter": "Datacenter",
    "serverstandardcore": "Standard",
    "serverdatacentercore": "Datacenter",
    "iotenterprise": "IoT Enterprise",
    "iotenterprises": "IoT Enterprise",
    "ppipro": "Pro",
}

# Длинные фразы раньше коротких: «Pro for Workstations» важнее «Pro».
_CAPTION_EDITIONS = (
    "Pro for Workstations",
    "IoT Enterprise LTSC",
    "IoT Enterprise",
    "Enterprise LTSC",
    "Enterprise",
    "Education",
    "Datacenter",
    "Standard",
    "Professional",
    "Home",
    "Pro",
)

# Известные билды клиентских Windows → DisplayVersion.
# 25H2 на 26100 — enablement package: без реестра DisplayVersion будет 24H2.
_BUILD_DISPLAY = {
    19041: "2004",
    19042: "20H2",
    19043: "21H1",
    19044: "21H2",
    19045: "22H2",
    22000: "21H2",
    22621: "22H2",
    22631: "23H2",
    26100: "24H2",
    26200: "25H2",
}

_DISPLAY_RE = re.compile(r"^(\d{2})\s*[hH]\s*(\d)$")
_SERVER_RE = re.compile(r"Windows\s+Server(?:\s+(\d{4}))?", re.IGNORECASE)


@dataclass(frozen=True)
class HardwareSnapshot:
    """Нормализованный снимок для devices / device_hardware_history."""

    cpu_name: str | None = None
    ram_gb: int | None = None
    disk_gb: int | None = None
    os_caption: str | None = None
    os_family: str | None = None
    os_edition: str | None = None
    os_display_version: str | None = None
    os_build: str | None = None

    @property
    def os_label(self) -> str:
        return format_os_label(self.os_family, self.os_edition, self.os_display_version)

    @property
    def identity(self) -> tuple:
        """Поля, по которым решаем, менялось ли железо."""
        return (
            self.cpu_name,
            self.ram_gb,
            self.disk_gb,
            self.os_family,
            self.os_edition,
            self.os_display_version,
            self.os_build,
        )

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.cpu_name,
                self.ram_gb,
                self.disk_gb,
                self.os_caption,
                self.os_family,
            )
        )


def bytes_to_gb(value: int | float | str | None) -> int | None:
    """Байты → целые ГиБ (1024³), округление до ближайшего.

    Ненулевой объём меньше 0.5 ГиБ даёт 1, чтобы флешка/крошечный диск
    не стал нулём.
    """
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        try:
            number = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
    if number < 0:
        return None
    if number == 0:
        return 0
    gb = int(round(number / _GIB))
    return gb if gb > 0 else 1


def normalize_cpu_name(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(str(value).split()).strip()
    return text[:255] or None


def join_cpu_names(names: list[str] | tuple[str, ...]) -> str | None:
    """Уникальные имена сокетов через « + »."""
    unique: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = normalize_cpu_name(raw)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    if not unique:
        return None
    return " + ".join(unique)[:255]


def normalize_display_version(value: str | None) -> str | None:
    """22h2 / 25H2 → 25H2. Прочее оставляем как есть (2004, 1809)."""
    text = (value or "").strip()
    if not text:
        return None
    match = _DISPLAY_RE.fullmatch(text)
    if match:
        return f"{match.group(1)}H{match.group(2)}"
    return text[:32]


def normalize_edition_id(value: str | None) -> str | None:
    if not value:
        return None
    key = re.sub(r"[^a-z0-9]", "", value.strip().lower())
    if not key:
        return None
    return _EDITION_ID.get(key)


def parse_build_number(value: str | int | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Version «10.0.26100» → 26100
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    digits = re.sub(r"\D", "", text)
    return digits[:32] or None


def map_build_to_display(build: str | int | None, *, family: str | None = None) -> str | None:
    """Запасной DisplayVersion по номеру сборки, если реестр молчит."""
    if family and "server" in family.lower():
        return None
    raw = parse_build_number(build)
    if not raw:
        return None
    try:
        number = int(raw)
    except ValueError:
        return None
    mapped = _BUILD_DISPLAY.get(number)
    if mapped:
        return mapped
    if number >= 27700:
        return "26H2"
    if number >= 26200:
        return "25H2"
    if number >= 26100:
        return "24H2"
    if 22000 <= number < 22621:
        return "21H2"
    return None


def parse_windows_caption(caption: str | None) -> tuple[str | None, str | None]:
    """Из Win32_OperatingSystem.Caption → (семейство, редакция)."""
    text = " ".join(str(caption or "").split()).strip()
    if not text:
        return None, None
    if text.lower().startswith("microsoft "):
        text = text[10:].strip()
    lower = text.lower()

    family: str | None = None
    server = _SERVER_RE.search(text)
    if server:
        year = server.group(1)
        family = f"Windows Server {year}" if year else "Windows Server"
    elif "windows 11" in lower:
        family = "Windows 11"
    elif "windows 10" in lower:
        family = "Windows 10"
    elif "windows 8.1" in lower:
        family = "Windows 8.1"
    elif "windows 8" in lower:
        family = "Windows 8"
    elif "windows 7" in lower:
        family = "Windows 7"
    elif text:
        family = text[:64]

    edition = None
    for phrase in _CAPTION_EDITIONS:
        if phrase.lower() in lower:
            edition = "Pro" if phrase == "Professional" else phrase
            break
    return family, edition


def format_os_label(
    family: str | None,
    edition: str | None = None,
    display_version: str | None = None,
) -> str:
    """Человекочитаемая строка: «Windows 11 Pro 25H2»."""
    parts: list[str] = []
    family_text = (family or "").strip()
    edition_text = (edition or "").strip()
    display = normalize_display_version(display_version)
    if family_text:
        parts.append(family_text)
    if edition_text and edition_text.lower() not in family_text.lower():
        parts.append(edition_text)
    if display and "server" not in family_text.lower():
        parts.append(display)
    return " ".join(parts)


def build_hardware_snapshot(
    *,
    cpu_name: str | None = None,
    ram_bytes: int | None = None,
    disk_bytes: int | None = None,
    os_caption: str | None = None,
    os_version: str | None = None,
    os_build: str | None = None,
    os_display_version: str | None = None,
    os_edition_id: str | None = None,
) -> HardwareSnapshot:
    """Собрать снимок из сырых полей WMI/реестра."""
    caption = " ".join(str(os_caption or "").split()).strip()[:255] or None
    family, caption_edition = parse_windows_caption(caption)
    edition = normalize_edition_id(os_edition_id) or caption_edition
    build = parse_build_number(os_build) or parse_build_number(os_version)
    display = normalize_display_version(os_display_version) or map_build_to_display(
        build, family=family
    )
    return HardwareSnapshot(
        cpu_name=normalize_cpu_name(cpu_name),
        ram_gb=bytes_to_gb(ram_bytes),
        disk_gb=bytes_to_gb(disk_bytes),
        os_caption=caption,
        os_family=family,
        os_edition=edition,
        os_display_version=display,
        os_build=build,
    )
