"""Короткоживущий билет VNC: IP и порт только из инвентаря, не из браузера.

Прокси (bawh-vnc) не ходит в БД — проверяет подпись SECRET_KEY.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

VNC_TOKEN_SALT = "bawh-vnc-v1"
VNC_TOKEN_MAX_AGE = 90
VNC_PORT_MIN = 5900
VNC_PORT_MAX = 5999
DEFAULT_VNC_PORT = 5900

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


class VncTokenError(ValueError):
    """Билет подделать нельзя, цель не из инвентаря или просрочен."""


@dataclass(frozen=True)
class VncTicket:
    device_id: int
    user_id: int
    ip: str
    port: int


def _secret_key() -> str:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            key = str(current_app.config.get("SECRET_KEY") or "").strip()
            if key:
                return key
    except Exception:  # noqa: BLE001 — воркер без Flask
        pass
    return str(os.environ.get("SECRET_KEY") or "").strip()


def _serializer() -> URLSafeTimedSerializer:
    secret = _secret_key()
    if not secret:
        raise VncTokenError("Не задан SECRET_KEY — билет VNC выдать нельзя.")
    return URLSafeTimedSerializer(secret, salt=VNC_TOKEN_SALT)


def validate_vnc_target(ip: str, port: int | str) -> tuple[str, int]:
    """Разрешить только конкретный адрес из карточки и порт 5900–5999."""
    text = (ip or "").strip()
    if not text:
        raise VncTokenError("У устройства нет IP.")
    if "%" in text or "/" in text or " " in text:
        raise VncTokenError("IP устройства выглядит некорректно.")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError as exc:
        raise VncTokenError("VNC только на IP из инвентаря, не на имя хоста.") from exc
    if addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_reserved:
        raise VncTokenError("Этот адрес нельзя использовать как цель VNC.")
    if not (addr.is_private or addr.is_global or addr.is_link_local):
        raise VncTokenError("Этот адрес нельзя использовать как цель VNC.")
    try:
        port_num = int(port)
    except (TypeError, ValueError) as exc:
        raise VncTokenError("Порт VNC должен быть числом.") from exc
    if port_num < VNC_PORT_MIN or port_num > VNC_PORT_MAX:
        raise VncTokenError(
            f"Порт VNC только {VNC_PORT_MIN}–{VNC_PORT_MAX} (обычно 5900)."
        )
    # Каноническая строка без ведущих нулей (10.0.0.01 → 10.0.0.1).
    compact = str(addr)
    if addr.version == 4 and not _IPV4_RE.fullmatch(text):
        # Пользователь мог передать сжатый вид — уже разобран ipaddress.
        pass
    return compact, port_num


def mint_ticket(*, device_id: int, user_id: int, ip: str, port: int) -> str:
    target_ip, target_port = validate_vnc_target(ip, port)
    payload = {
        "d": int(device_id),
        "u": int(user_id),
        "ip": target_ip,
        "p": target_port,
        "v": 1,
    }
    return _serializer().dumps(payload)


def load_ticket(token: str, *, max_age: int = VNC_TOKEN_MAX_AGE) -> VncTicket:
    raw = (token or "").strip()
    if not raw:
        raise VncTokenError("Нет билета VNC.")
    try:
        payload = _serializer().loads(raw, max_age=max_age)
    except SignatureExpired as exc:
        raise VncTokenError("Билет VNC просрочен — обновите страницу.") from exc
    except BadSignature as exc:
        raise VncTokenError("Билет VNC недействителен.") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise VncTokenError("Билет VNC недействителен.")
    try:
        device_id = int(payload["d"])
        user_id = int(payload["u"])
        ip = str(payload["ip"])
        port = int(payload["p"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VncTokenError("Билет VNC недействителен.") from exc
    ip, port = validate_vnc_target(ip, port)
    return VncTicket(device_id=device_id, user_id=user_id, ip=ip, port=port)
