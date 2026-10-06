"""Разбор IP и CIDR.

Валидация здесь, а не в маршрутах: и форма сектора, и опрос
должны одинаково понимать, какой адрес допустим.
В subprocess адрес попадает только после ipaddress, поэтому
строка вроде «8.8.8.8; rm -rf» не станет аргументом shell.
"""

import ipaddress
import re
import socket

from flask import current_app


class NetworkInputError(ValueError):
    """Понятная ошибка для формы: что не так с введённым диапазоном."""


# Одно имя или FQDN: метки из букв/цифр/дефиса, без пробелов и shell-символов.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)


def min_cidr_prefix() -> int:
    return int(current_app.config.get("MIN_CIDR_PREFIX", 22))


def parse_range(text: str) -> ipaddress.IPv4Address | ipaddress.IPv4Network:
    """Возвращает адрес или сеть. Слишком крупный CIDR отклоняет.

    strict=False позволяет записать 10.0.1.15/24: ipaddress сам
    приведёт это к 10.0.1.0/24.
    """
    raw = (text or "").strip()
    if not raw:
        raise NetworkInputError("Пустая строка диапазона.")
    try:
        if "/" in raw:
            network = ipaddress.ip_network(raw, strict=False)
            if not isinstance(network, ipaddress.IPv4Network):
                raise NetworkInputError("Пока поддерживается только IPv4.")
            prefix = min_cidr_prefix()
            if network.prefixlen < prefix:
                raise NetworkInputError(
                    f"Сеть {network} слишком большая. Минимальный префикс /{prefix}."
                )
            return network
        address = ipaddress.ip_address(raw)
    except NetworkInputError:
        raise
    except ValueError as exc:
        raise NetworkInputError(f"Некорректный IP или CIDR: {raw}") from exc
    if not isinstance(address, ipaddress.IPv4Address):
        raise NetworkInputError("Пока поддерживается только IPv4.")
    return address


def range_to_text(value: ipaddress.IPv4Address | ipaddress.IPv4Network) -> str:
    return str(value)


def iter_host_ips(value: ipaddress.IPv4Address | ipaddress.IPv4Network) -> list[str]:
    """Разворачивает диапазон в список адресов хостов.

    Для сети крупнее /31 не включаем адрес сети и broadcast —
    их обычно не пингуют. /32 и одиночный IP возвращаются как есть.
    """
    if isinstance(value, ipaddress.IPv4Address):
        return [str(value)]
    if value.prefixlen >= 31:
        return [str(host) for host in value]
    return [str(host) for host in value.hosts()]


def expand_ranges(texts: list[str], limit: int | None = None) -> list[str]:
    """Уникальные IP из нескольких строк, в порядке появления.

    limit защищает опрос от случайного огромного списка.
    """
    if limit is None:
        limit = int(current_app.config.get("MAX_HOSTS_PER_POLL", 2048))
    seen: set[str] = set()
    ordered: list[str] = []
    for text in texts:
        for ip in iter_host_ips(parse_range(text)):
            if ip in seen:
                continue
            seen.add(ip)
            ordered.append(ip)
            if len(ordered) > limit:
                raise NetworkInputError(
                    f"В диапазоне больше {limit} адресов. Уменьшите CIDR или поднимите MAX_HOSTS_PER_POLL."
                )
    return ordered


def assert_public_ipv4(ip: str) -> str:
    """Проверяет, что строка — один IPv4, и возвращает её канонический вид.

    Имя функции историческое: адрес может быть и частным (10/8, 192.168/16).
    Смысл — «ровно один адрес, без CIDR и посторонних символов».
    """
    raw = (ip or "").strip()
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise NetworkInputError(f"Некорректный IP: {raw}") from exc
    if not isinstance(address, ipaddress.IPv4Address):
        raise NetworkInputError("Пока поддерживается только IPv4.")
    return str(address)


def assert_host_or_ipv4(value: str) -> str:
    """IPv4 или DNS-имя (короткое / FQDN). Возвращает нормализованную строку."""
    raw = (value or "").strip().rstrip(".")
    if not raw:
        raise NetworkInputError("Укажите IP или DNS-имя.")
    try:
        return assert_public_ipv4(raw)
    except NetworkInputError:
        pass
    if not _HOSTNAME_RE.match(raw):
        raise NetworkInputError(f"Некорректный IP или DNS-имя: {value}")
    return raw.lower()


def resolve_to_ipv4(host: str, timeout_s: float = 2.0) -> str:
    """Резолвит IP или DNS-имя в один IPv4 для последующего ping.

    DNS-имя сначала проходит assert_host_or_ipv4, чтобы в getaddrinfo
    не ушла строка с посторонними символами.
    """
    target = assert_host_or_ipv4(host)
    try:
        ipaddress.IPv4Address(target)
        return target
    except ValueError:
        pass
    previous = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout_s)
        infos = socket.getaddrinfo(target, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkInputError(f"Не удалось разрешить имя: {target}") from exc
    finally:
        socket.setdefaulttimeout(previous)
    if not infos:
        raise NetworkInputError(f"Не удалось разрешить имя: {target}")
    return assert_public_ipv4(infos[0][4][0])


def source_ipv4_toward(dest_ip: str) -> str:
    """IPv4 этого хоста, с которого ядро уйдёт на dest_ip (без реальной отправки)."""
    target = assert_public_ipv4(dest_ip)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target, 9))
        source = sock.getsockname()[0]
    except OSError as exc:
        raise NetworkInputError(
            f"Не удалось определить исходящий IP к {target}."
        ) from exc
    finally:
        sock.close()
    if not source or source.startswith("127."):
        raise NetworkInputError(
            f"Исходящий адрес к {target} похож на loopback ({source})."
        )
    return assert_public_ipv4(source)


def cidr_to_wildcard(cidr: str | None) -> str | None:
    """192.168.15.0/24 → 192.168.15.* ; /16 → 192.168.*.* ; /32 → сам адрес."""
    raw = (cidr or "").strip()
    if not raw:
        return None
    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError:
        return None
    if not isinstance(network, ipaddress.IPv4Network):
        return str(network)
    octets = str(network.network_address).split(".")
    prefix = network.prefixlen
    if prefix >= 32:
        return str(network.network_address)
    if prefix >= 24:
        return f"{octets[0]}.{octets[1]}.{octets[2]}.*"
    if prefix >= 16:
        return f"{octets[0]}.{octets[1]}.*.*"
    if prefix >= 8:
        return f"{octets[0]}.*.*.*"
    return "*.*.*.*"


def sector_subnet_labels(sector, devices=None) -> list[str]:
    """Подсети сектора для шапки карты: сначала CIDR секторов, иначе /24 устройств."""
    labels: list[str] = []
    seen: set[str] = set()
    for item in getattr(sector, "ranges", None) or []:
        label = cidr_to_wildcard(getattr(item, "cidr", None))
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    if labels:
        return labels
    for device in devices or []:
        ip = (getattr(device, "ip", None) or "").strip()
        parts = ip.split(".")
        if len(parts) != 4:
            continue
        try:
            if not all(0 <= int(part) <= 255 for part in parts):
                continue
        except ValueError:
            continue
        label = f"{parts[0]}.{parts[1]}.{parts[2]}.*"
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def normalize_mac(value: str | None) -> str | None:
    """Приводит MAC к AA:BB:CC:DD:EE:FF. Мусор возвращает как None."""
    if not value:
        return None
    hexdigits = "".join(ch for ch in value if ch.isalnum())
    if len(hexdigits) != 12:
        return None
    try:
        int(hexdigits, 16)
    except ValueError:
        return None
    pairs = [hexdigits[i : i + 2] for i in range(0, 12, 2)]
    return ":".join(pair.upper() for pair in pairs)
