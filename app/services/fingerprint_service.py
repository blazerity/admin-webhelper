"""Короткий проход TCP/SNMP для серых типов устройств.

Имена AD и WMI-серийник важнее. Этот зонд закрывает камеры, MikroTik и МФУ
без своего префикса: параллельные TCP-пробы и SNMPv1 sysDescr (community public).
Новых зависимостей нет — сырой UDP/BER.

Порты: 445/135 Windows, 8728/8291 MikroTik, 9100/515 печать, 554 камера.
"""

from __future__ import annotations

import logging
import select
import socket
import time
from dataclasses import dataclass

from app.services.net_utils import assert_public_ipv4

logger = logging.getLogger(__name__)

FP_WINDOWS = "windows"
FP_ROUTER = "router"
FP_CAMERA = "camera"
FP_PRINTER = "printer"

TCP_PORTS = (445, 135, 8728, 8291, 9100, 515, 554)
_WINDOWS_PORTS = frozenset({445, 135})
_ROUTER_PORTS = frozenset({8728, 8291})
_PRINTER_PORTS = frozenset({9100, 515})
_CAMERA_PORTS = frozenset({554})

_TCP_TIMEOUT_S = 0.4
_SNMP_TIMEOUT_S = 0.5
_SNMP_PORT = 161
_SNMP_COMMUNITY = b"public"
_SYSDESCR_OID = (1, 3, 6, 1, 2, 1, 1, 1, 0)
_SYSDESCR_OID_BER = bytes([0x06, 0x08, 0x2B, 0x06, 0x01, 0x02, 0x01, 0x01, 0x01, 0x00])

_SNMP_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        FP_ROUTER,
        ("mikrotik", "routeros", "routerboard", "swos"),
    ),
    (
        FP_CAMERA,
        ("hikvision", "dahua", "axis", "onvif", "ip camera", "ipcamera", "ipc"),
    ),
    (
        FP_PRINTER,
        (
            "laserjet",
            "officejet",
            "deskjet",
            "jetdirect",
            "xerox",
            "kyocera",
            "ricoh",
            "brother",
            "canon",
            "printer",
            "mfp",
            "multifunction",
        ),
    ),
    (
        FP_WINDOWS,
        ("windows", "microsoft", "workgroup"),
    ),
)


@dataclass(frozen=True)
class Fingerprint:
    """Итог одного зонда. kind — windows/router/camera/printer или None."""

    kind: str | None
    detail: str | None


def fingerprint_host(ip: str) -> Fingerprint:
    """TCP + SNMP по одному адресу. Вызывать из воркера опроса, не из веб-потока."""
    ip = assert_public_ipv4(ip)
    open_ports = probe_tcp_ports(ip)
    sysdescr = probe_snmp_sysdescr(ip)
    kind = _kind_from_snmp(sysdescr) or _kind_from_ports(open_ports)
    detail = _detail_text(open_ports, sysdescr)
    if not kind:
        return Fingerprint(None, detail or None)
    return Fingerprint(kind, detail or kind)


def probe_tcp_ports(
    ip: str,
    ports: tuple[int, ...] = TCP_PORTS,
    timeout_s: float = _TCP_TIMEOUT_S,
) -> tuple[int, ...]:
    """Какие порты приняли TCP. Один общий таймаут, без вложенного пула потоков."""
    sockets: list[socket.socket] = []
    pending: dict[socket.socket, int] = {}
    opened: list[int] = []
    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            try:
                sock.connect((ip, port))
            except BlockingIOError:
                pending[sock] = port
                sockets.append(sock)
            except OSError:
                sock.close()
            else:
                opened.append(port)
                sock.close()
        deadline = time.monotonic() + timeout_s
        while pending:
            wait = deadline - time.monotonic()
            if wait <= 0:
                break
            _, writable, _ = select.select([], list(pending), [], wait)
            if not writable:
                continue
            for sock in writable:
                port = pending.pop(sock, None)
                if port is None:
                    continue
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err == 0:
                    opened.append(port)
                try:
                    sock.close()
                except OSError:
                    pass
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass
    return tuple(sorted(set(opened)))


def probe_snmp_sysdescr(ip: str, timeout_s: float = _SNMP_TIMEOUT_S) -> str | None:
    """SNMPv1 GET sysDescr.0, community public. None — нет ответа или мусор."""
    request = _snmp_get_request(_SYSDESCR_OID, _SNMP_COMMUNITY)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_s)
        sock.sendto(request, (ip, _SNMP_PORT))
        payload, _addr = sock.recvfrom(2048)
    except OSError:
        return None
    finally:
        sock.close()
    return _parse_snmp_sysdescr(payload)


def _kind_from_ports(open_ports: tuple[int, ...]) -> str | None:
    ports = set(open_ports)
    if ports & _WINDOWS_PORTS:
        return FP_WINDOWS
    if ports & _ROUTER_PORTS:
        return FP_ROUTER
    if ports & _PRINTER_PORTS:
        return FP_PRINTER
    if ports & _CAMERA_PORTS:
        return FP_CAMERA
    return None


def _kind_from_snmp(sysdescr: str | None) -> str | None:
    text = (sysdescr or "").strip().lower()
    if not text:
        return None
    for kind, hints in _SNMP_HINTS:
        if any(hint in text for hint in hints):
            return kind
    return None


def _detail_text(open_ports: tuple[int, ...], sysdescr: str | None) -> str:
    parts: list[str] = []
    if open_ports:
        parts.append("tcp:" + ",".join(str(port) for port in open_ports))
    descr = " ".join((sysdescr or "").split())
    if descr:
        if len(descr) > 180:
            descr = descr[:180] + "…"
        parts.append("snmp:" + descr)
    return "; ".join(parts)[:255]


def _snmp_get_request(oid: tuple[int, ...], community: bytes) -> bytes:
    varbind = _ber_seq(0x30, _ber_oid(oid), b"\x05\x00")
    varbinds = _ber_seq(0x30, varbind)
    pdu = _ber_seq(0xA0, _ber_int(1), _ber_int(0), _ber_int(0), varbinds)
    return _ber_seq(0x30, _ber_int(0), _ber_octet(community), pdu)


def _parse_snmp_sysdescr(payload: bytes) -> str | None:
    marker = _SYSDESCR_OID_BER
    idx = payload.find(marker)
    if idx < 0:
        return None
    value, _rest = _ber_read(payload[idx + len(marker) :])
    if value is None:
        return None
    tag, body = value
    if tag != 0x04:
        return None
    text = body.decode("utf-8", errors="replace").strip()
    return text or None


def _ber_read(buf: bytes) -> tuple[tuple[int, bytes] | None, bytes]:
    if len(buf) < 2:
        return None, buf
    tag = buf[0]
    length, rest = _ber_read_length(buf[1:])
    if length is None or len(rest) < length:
        return None, buf
    return (tag, rest[:length]), rest[length:]


def _ber_read_length(buf: bytes) -> tuple[int | None, bytes]:
    if not buf:
        return None, buf
    first = buf[0]
    if first < 0x80:
        return first, buf[1:]
    count = first & 0x7F
    if count == 0 or count > 4 or len(buf) < 1 + count:
        return None, buf
    return int.from_bytes(buf[1 : 1 + count], "big"), buf[1 + count :]


def _ber_len(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    body = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _ber_int(value: int) -> bytes:
    if value == 0:
        body = b"\x00"
    else:
        length = max(1, (value.bit_length() + 8) // 8)
        body = value.to_bytes(length, "big", signed=True)
        while len(body) > 1 and (
            (body[0] == 0x00 and body[1] < 0x80)
            or (body[0] == 0xFF and body[1] >= 0x80)
        ):
            body = body[1:]
    return b"\x02" + _ber_len(len(body)) + body


def _ber_octet(data: bytes) -> bytes:
    return b"\x04" + _ber_len(len(data)) + data


def _ber_seq(tag: int, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return bytes([tag]) + _ber_len(len(body)) + body


def _ber_oid(oids: tuple[int, ...]) -> bytes:
    if len(oids) < 2:
        raise ValueError("OID слишком короткий")
    chunks = [bytes([40 * oids[0] + oids[1]])]
    for number in oids[2:]:
        if number < 0:
            raise ValueError("OID не может быть отрицательным")
        if number < 128:
            chunks.append(bytes([number]))
            continue
        digits: list[int] = []
        while number:
            digits.append(number & 0x7F)
            number >>= 7
        for i, digit in enumerate(reversed(digits)):
            if i < len(digits) - 1:
                chunks.append(bytes([digit | 0x80]))
            else:
                chunks.append(bytes([digit]))
    body = b"".join(chunks)
    return b"\x06" + _ber_len(len(body)) + body
