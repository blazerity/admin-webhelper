"""Имя хоста и MAC без сканера пакетов.

Две разные проверки, их легко перепутать:

* Имя (lookup_hostname) — вопрос к DNS: «какой PTR у этого IP?».
  Сервер и устройство могут быть в разных подсетях, DNS всё равно ответит,
  если запись заведена.
* MAC (lookup_mac) — адрес канального уровня. Его видно только когда
  этот сервер и устройство сидят в одном L2-сегменте: коммутатор
  доставил кадр, и ядро записало соседа в таблицу ARP (Windows)
  или neighbor (Linux). Через маршрутизатор MAC чужой сети не приходит.

Scapy здесь не используем. Ему нужен захват пакетов и дополнительные
права (raw socket / Npcap). Для первой версии достаточно прочитать
таблицу, которую ядро уже ведёт само. Пинг перед этим как раз заполняет
строку ARP, если хост ответил по ICMP.

В subprocess адрес попадает только после assert_public_ipv4.
shell=True нет: иначе «10.0.0.5; whoami» стало бы второй командой.
"""

import os
import re
import socket
import subprocess

from app.services.net_utils import assert_public_ipv4, normalize_mac

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
