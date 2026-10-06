"""Разбор PEM-сертификата и ключа для HTTPS из веб-настроек."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from cryptography.x509.oid import NameOID


class TlsPemError(ValueError):
    """Сертификат или ключ не приняты."""


@dataclass(frozen=True)
class TlsCertInfo:
    subject: str
    issuer: str
    not_after: datetime
    fingerprint_sha256: str
    san: str
    fullchain_pem: str
    key_pem: str


def _cn(name: x509.Name) -> str:
    parts = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    if parts:
        return str(parts[0].value)
    return name.rfc4514_string() or "—"


def _public_bytes(key) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def parse_tls_material(cert_pem: str, key_pem: str) -> TlsCertInfo:
    cert_text = (cert_pem or "").strip()
    key_text = (key_pem or "").strip()
    if "BEGIN CERTIFICATE" not in cert_text:
        raise TlsPemError("Вставьте PEM-сертификат (BEGIN CERTIFICATE).")
    if "BEGIN" not in key_text or "PRIVATE KEY" not in key_text:
        raise TlsPemError("Вставьте PEM-ключ (BEGIN PRIVATE KEY или RSA PRIVATE KEY).")
    try:
        cert = x509.load_pem_x509_certificate(cert_text.encode("utf-8"))
    except ValueError as exc:
        raise TlsPemError("Не удалось разобрать сертификат.") from exc
    try:
        key = serialization.load_pem_private_key(key_text.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise TlsPemError(
            "Не удалось разобрать ключ. Поддерживается ключ без парольной фразы."
        ) from exc
    if not isinstance(key, (rsa.RSAPrivateKey, dsa.DSAPrivateKey, ec.EllipticCurvePrivateKey,
                            ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)):
        raise TlsPemError("Тип закрытого ключа не поддерживается.")
    if _public_bytes(cert.public_key()) != _public_bytes(key.public_key()):
        raise TlsPemError("Ключ не соответствует сертификату.")
    not_after = getattr(cert, "not_valid_after_utc", None)
    if not_after is None:
        not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    san = ""
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = ext.value.get_values_for_type(x509.DNSName)
        san = ", ".join(names)
    except x509.ExtensionNotFound:
        san = ""
    fingerprint = hashlib.sha256(
        cert.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    # Нормализуем: сертификат(ы) + ключ PKCS8 без шифрования.
    normalized_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return TlsCertInfo(
        subject=_cn(cert.subject),
        issuer=_cn(cert.issuer),
        not_after=not_after,
        fingerprint_sha256=fingerprint,
        san=san,
        fullchain_pem=cert_text + ("\n" if not cert_text.endswith("\n") else ""),
        key_pem=normalized_key,
    )
