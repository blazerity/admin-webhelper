"""Состояние HTTPS из Параметров (метаданные, без закрытого ключа в БД)."""

from __future__ import annotations

from dataclasses import dataclass

from app.extensions import db
from app.utils import as_truthy

TLS_HTTPS_ENABLED = "tls_https_enabled"
TLS_REDIRECT_HTTP = "tls_redirect_http"
TLS_SERVER_NAME = "tls_server_name"
TLS_SECURE_COOKIE = "tls_secure_cookie"
TLS_CERT_SUBJECT = "tls_cert_subject"
TLS_CERT_ISSUER = "tls_cert_issuer"
TLS_CERT_NOT_AFTER = "tls_cert_not_after"
TLS_CERT_FINGERPRINT = "tls_cert_fingerprint"
TLS_CERT_SAN = "tls_cert_san"
TLS_CERT_PRESENT = "tls_cert_present"


@dataclass(frozen=True)
class TlsSettings:
    https_enabled: bool
    redirect_http: bool
    server_name: str
    secure_cookie: bool
    cert_present: bool
    subject: str
    issuer: str
    not_after: str
    fingerprint: str
    san: str


def _get_raw(key: str) -> str | None:
    from app.services.settings_service import get_app_setting

    return get_app_setting(key)


def _set_raw(key: str, value: str) -> None:
    from app.services.settings_service import set_app_setting

    set_app_setting(key, value)


def get_tls_settings() -> TlsSettings:
    return TlsSettings(
        https_enabled=as_truthy(_get_raw(TLS_HTTPS_ENABLED) or "0", default=False),
        redirect_http=as_truthy(_get_raw(TLS_REDIRECT_HTTP) or "0", default=False),
        server_name=(_get_raw(TLS_SERVER_NAME) or "").strip(),
        secure_cookie=as_truthy(_get_raw(TLS_SECURE_COOKIE) or "0", default=False),
        cert_present=as_truthy(_get_raw(TLS_CERT_PRESENT) or "0", default=False),
        subject=(_get_raw(TLS_CERT_SUBJECT) or "").strip(),
        issuer=(_get_raw(TLS_CERT_ISSUER) or "").strip(),
        not_after=(_get_raw(TLS_CERT_NOT_AFTER) or "").strip(),
        fingerprint=(_get_raw(TLS_CERT_FINGERPRINT) or "").strip(),
        san=(_get_raw(TLS_CERT_SAN) or "").strip(),
    )


def save_tls_settings(
    *,
    https_enabled: bool | None = None,
    redirect_http: bool | None = None,
    server_name: str | None = None,
    secure_cookie: bool | None = None,
    cert_present: bool | None = None,
    subject: str | None = None,
    issuer: str | None = None,
    not_after: str | None = None,
    fingerprint: str | None = None,
    san: str | None = None,
) -> TlsSettings:
    if https_enabled is not None:
        _set_raw(TLS_HTTPS_ENABLED, "1" if https_enabled else "0")
    if redirect_http is not None:
        _set_raw(TLS_REDIRECT_HTTP, "1" if redirect_http else "0")
    if server_name is not None:
        _set_raw(TLS_SERVER_NAME, server_name.strip())
    if secure_cookie is not None:
        _set_raw(TLS_SECURE_COOKIE, "1" if secure_cookie else "0")
    if cert_present is not None:
        _set_raw(TLS_CERT_PRESENT, "1" if cert_present else "0")
    if subject is not None:
        _set_raw(TLS_CERT_SUBJECT, subject)
    if issuer is not None:
        _set_raw(TLS_CERT_ISSUER, issuer)
    if not_after is not None:
        _set_raw(TLS_CERT_NOT_AFTER, not_after)
    if fingerprint is not None:
        _set_raw(TLS_CERT_FINGERPRINT, fingerprint)
    if san is not None:
        _set_raw(TLS_CERT_SAN, san)
    db.session.commit()
    return get_tls_settings()
