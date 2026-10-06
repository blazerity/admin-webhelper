"""Ключи app_settings для веб-VNC (экспериментальный шлюз)."""

from __future__ import annotations

from dataclasses import dataclass

from app.extensions import db
from app.services.crypto_service import CryptoError, CryptoNotConfigured, decrypt, encrypt
from app.services.vnc_token import DEFAULT_VNC_PORT, VNC_PORT_MAX, VNC_PORT_MIN
from app.utils import as_truthy

VNC_GATEWAY_ENABLED = "vnc_gateway_enabled"
VNC_PORT_KEY = "vnc_port"
VNC_PASSWORD_KEY = "vnc_password_encrypted"


@dataclass(frozen=True)
class VncSettings:
    gateway_enabled: bool
    port: int
    password_set: bool
    password: str = ""


def _get_raw(key: str) -> str | None:
    from app.services.settings_service import get_app_setting

    return get_app_setting(key)


def _set_raw(key: str, value: str) -> None:
    from app.services.settings_service import set_app_setting

    set_app_setting(key, value)


def get_vnc_settings(*, include_password: bool = False) -> VncSettings:
    raw_enabled = _get_raw(VNC_GATEWAY_ENABLED)
    if raw_enabled is None or not str(raw_enabled).strip():
        gateway_enabled = True
    else:
        gateway_enabled = as_truthy(str(raw_enabled).strip(), default=True)
    raw_port = (_get_raw(VNC_PORT_KEY) or "").strip()
    try:
        port = int(raw_port) if raw_port else DEFAULT_VNC_PORT
    except ValueError:
        port = DEFAULT_VNC_PORT
    if port < VNC_PORT_MIN or port > VNC_PORT_MAX:
        port = DEFAULT_VNC_PORT
    blob = (_get_raw(VNC_PASSWORD_KEY) or "").strip()
    password = ""
    if include_password and blob:
        try:
            password = decrypt(blob)
        except (CryptoError, CryptoNotConfigured):
            password = ""
    return VncSettings(
        gateway_enabled=gateway_enabled,
        port=port,
        password_set=bool(blob),
        password=password,
    )


def set_vnc_settings(
    *,
    gateway_enabled: bool | None = None,
    port: int | str | None = None,
    password: str | None = None,
) -> VncSettings:
    if gateway_enabled is not None:
        _set_raw(VNC_GATEWAY_ENABLED, "1" if gateway_enabled else "0")
    if port is not None:
        try:
            port_num = int(str(port).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("Порт VNC должен быть целым числом.") from exc
        if port_num < VNC_PORT_MIN or port_num > VNC_PORT_MAX:
            raise ValueError(f"Порт VNC: {VNC_PORT_MIN}–{VNC_PORT_MAX}.")
        _set_raw(VNC_PORT_KEY, str(port_num))
    if password is not None:
        secret = password.strip()
        if secret:
            _set_raw(VNC_PASSWORD_KEY, encrypt(secret))
        else:
            # Пустое поле — не трогать уже сохранённый пароль.
            pass
    db.session.commit()
    return get_vnc_settings()


def clear_vnc_password() -> VncSettings:
    _set_raw(VNC_PASSWORD_KEY, "")
    db.session.commit()
    return get_vnc_settings()
