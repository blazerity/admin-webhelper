"""Запись PEM на диск и вызов deploy/apply-nginx-tls.sh от root."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from flask import current_app, has_app_context

from app.services.systemd_service import _run_privileged_script, resolve_sudo_credentials
from app.services.tls_pem import TlsCertInfo, TlsPemError, parse_tls_material
from app.services.tls_settings import get_tls_settings, save_tls_settings

logger = logging.getLogger(__name__)

_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class TlsApplyError(RuntimeError):
    """Не удалось применить Nginx/TLS."""


def _project_root() -> Path:
    if has_app_context():
        root = current_app.config.get("PROJECT_ROOT")
        if root:
            return Path(str(root))
    return Path(os.environ.get("PROJECT_ROOT") or Path(__file__).resolve().parents[2])


def incoming_dir() -> Path:
    return _project_root() / "certs" / "incoming"


def validate_server_name(name: str) -> str:
    text = (name or "").strip() or "_"
    if text != "_" and not _SERVER_NAME_RE.fullmatch(text):
        raise TlsPemError(
            "Имя сайта: буквы, цифры, точка, дефис и подчёркивание, либо _."
        )
    return text


def _write_incoming(info: TlsCertInfo | None, *, enable: bool, redirect: bool,
                    server_name: str, secure_cookie: bool) -> Path:
    folder = incoming_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    if info is not None:
        cert_path = folder / "fullchain.pem"
        key_path = folder / "privkey.pem"
        cert_path.write_text(info.fullchain_pem, encoding="utf-8")
        key_path.write_text(info.key_pem, encoding="utf-8")
        os.chmod(cert_path, 0o600)
        os.chmod(key_path, 0o600)
    req = folder / "request.env"
    lines = [
        f"ENABLE_HTTPS={'1' if enable else '0'}",
        f"REDIRECT_HTTP={'1' if redirect else '0'}",
        f"SERVER_NAME={server_name}",
        f"SET_SECURE_COOKIE={'1' if secure_cookie else '0'}",
        "",
    ]
    req.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(req, 0o600)
    return folder


def save_certificate_files(cert_pem: str, key_pem: str, server_name: str) -> TlsCertInfo:
    info = parse_tls_material(cert_pem, key_pem)
    name = validate_server_name(server_name)
    current = get_tls_settings()
    _write_incoming(
        info,
        enable=current.https_enabled,
        redirect=current.redirect_http,
        server_name=name,
        secure_cookie=current.secure_cookie,
    )
    save_tls_settings(
        server_name=name,
        cert_present=True,
        subject=info.subject,
        issuer=info.issuer,
        not_after=info.not_after.isoformat(),
        fingerprint=info.fingerprint_sha256,
        san=info.san,
    )
    return info


def apply_nginx_tls(*, enable: bool, redirect: bool, server_name: str,
                    secure_cookie: bool, cert_pem: str = "", key_pem: str = "") -> str:
    """Сохранить PEM при наличии и вызвать privileged-скрипт Nginx."""
    name = validate_server_name(server_name)
    info: TlsCertInfo | None = None
    cert_text = (cert_pem or "").strip()
    key_text = (key_pem or "").strip()
    if cert_text or key_text:
        info = parse_tls_material(cert_text, key_text)
    elif enable:
        cert_file = incoming_dir() / "fullchain.pem"
        key_file = incoming_dir() / "privkey.pem"
        if not cert_file.is_file() or not key_file.is_file():
            raise TlsPemError(
                "Сначала сохраните сертификат и ключ, либо вставьте их в форму."
            )
        info = parse_tls_material(cert_file.read_text(encoding="utf-8"),
                                  key_file.read_text(encoding="utf-8"))
    if enable and info is None:
        raise TlsPemError("Нет сертификата для включения HTTPS.")

    _write_incoming(
        info,
        enable=enable,
        redirect=redirect,
        server_name=name,
        secure_cookie=secure_cookie,
    )
    if info is not None:
        save_tls_settings(
            https_enabled=enable,
            redirect_http=redirect,
            server_name=name,
            secure_cookie=secure_cookie,
            cert_present=True,
            subject=info.subject,
            issuer=info.issuer,
            not_after=info.not_after.isoformat(),
            fingerprint=info.fingerprint_sha256,
            san=info.san,
        )
    else:
        save_tls_settings(
            https_enabled=False,
            redirect_http=False,
            server_name=name,
            secure_cookie=False,
        )

    root = _project_root()
    script = root / "deploy" / "apply-nginx-tls.sh"
    if not script.is_file():
        raise TlsApplyError(f"Нет скрипта {script}")
    sudo_user, sudo_password = resolve_sudo_credentials()
    result = _run_privileged_script(
        str(script),
        [str(root)],
        sudo_user=sudo_user,
        sudo_password=sudo_password,
        timeout=90,
    )
    detail = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        logger.warning("apply-nginx-tls failed: %s", detail)
        raise TlsApplyError(
            f"Nginx не применил TLS (код {result.returncode}). {detail}".strip()
            + " Проверьте sudo-учётку в Настройки → Общие "
            "и deploy/bawh-update.sudoers."
        )
    if enable:
        return (
            f"HTTPS включён для {name}. Сертификат: {info.subject if info else name}."
            + (" HTTP перенаправляется на HTTPS." if redirect else "")
        )
    return "HTTPS выключен, сайт снова слушает только HTTP :80."
