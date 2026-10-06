"""Билет VNC: только IP из инвентаря, порт 5900–5999, подпись SECRET_KEY.

Запуск: python -m unittest tests.test_vnc_token
"""

from __future__ import annotations

import os
import unittest

from app.services.vnc_token import VncTokenError, load_ticket, mint_ticket, validate_vnc_target


class VncTargetTests(unittest.TestCase):
    def test_accepts_private_ipv4_and_default_port(self) -> None:
        ip, port = validate_vnc_target("10.0.0.50", 5900)
        self.assertEqual(ip, "10.0.0.50")
        self.assertEqual(port, 5900)

    def test_rejects_loopback_and_hostname(self) -> None:
        with self.assertRaises(VncTokenError):
            validate_vnc_target("127.0.0.1", 5900)
        with self.assertRaises(VncTokenError):
            validate_vnc_target("pc-lab.example.com", 5900)
        with self.assertRaises(VncTokenError):
            validate_vnc_target("0.0.0.0", 5900)

    def test_rejects_ports_outside_vnc_range(self) -> None:
        with self.assertRaises(VncTokenError):
            validate_vnc_target("10.0.0.50", 22)
        with self.assertRaises(VncTokenError):
            validate_vnc_target("10.0.0.50", 445)
        with self.assertRaises(VncTokenError):
            validate_vnc_target("10.0.0.50", 6000)


class VncTicketTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["SECRET_KEY"] = "test-vnc-secret"

    def test_roundtrip(self) -> None:
        token = mint_ticket(device_id=7, user_id=3, ip="192.168.10.4", port=5901)
        ticket = load_ticket(token)
        self.assertEqual(ticket.device_id, 7)
        self.assertEqual(ticket.user_id, 3)
        self.assertEqual(ticket.ip, "192.168.10.4")
        self.assertEqual(ticket.port, 5901)

    def test_tamper_fails(self) -> None:
        token = mint_ticket(device_id=1, user_id=1, ip="10.1.1.8", port=5900)
        with self.assertRaises(VncTokenError):
            load_ticket(token + "x")

    def test_wrong_secret_fails(self) -> None:
        token = mint_ticket(device_id=1, user_id=1, ip="10.1.1.8", port=5900)
        os.environ["SECRET_KEY"] = "other-secret"
        with self.assertRaises(VncTokenError):
            load_ticket(token)


if __name__ == "__main__":
    unittest.main()
