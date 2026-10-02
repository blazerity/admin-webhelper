"""Разбор IP и CIDR.

Валидация здесь, а не в маршрутах: и форма сектора, и опрос
должны одинаково понимать, какой адрес допустим.
В subprocess адрес попадает только после ipaddress, поэтому
строка вроде «8.8.8.8; rm -rf» не станет аргументом shell.
"""

import ipaddress

from flask import current_app


class NetworkInputError(ValueError):
    """Понятная ошибка для формы: что не так с введённым диапазоном."""


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
