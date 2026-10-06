"""PEM сертификата и ключа для HTTPS из Параметров.

Запуск: python -m unittest tests.test_tls_pem
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services.tls_pem import TlsPemError, parse_tls_material


def _pair(cn: str = "bawh.example.com") -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(cn)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode("ascii")
    return cert_pem, key_pem


class TlsPemTests(unittest.TestCase):
    def test_matching_pair(self) -> None:
        cert, key = _pair()
        info = parse_tls_material(cert, key)
        self.assertEqual(info.subject, "bawh.example.com")
        self.assertIn("bawh.example.com", info.san)
        self.assertIn("BEGIN CERTIFICATE", info.fullchain_pem)
        self.assertIn("BEGIN PRIVATE KEY", info.key_pem)
        self.assertEqual(len(info.fingerprint_sha256), 64)

    def test_mismatch_and_garbage(self) -> None:
        cert_a, _key_a = _pair("a.example.com")
        _cert_b, key_b = _pair("b.example.com")
        with self.assertRaises(TlsPemError):
            parse_tls_material(cert_a, key_b)
        with self.assertRaises(TlsPemError):
            parse_tls_material("not a cert", "not a key")


if __name__ == "__main__":
    unittest.main()
