"""Быстрая проверка устройства: ICMP → device_history + статус.

Запуск: python -m unittest tests.test_device_check
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from app import create_app
from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, Sector
from app.schema import ensure_schema
from app.services.ping_service import PingResult, check_device


class DeviceCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app("testing")

    def setUp(self) -> None:
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        ensure_schema()
        sector = Sector(name="lab", description="")
        db.session.add(sector)
        db.session.flush()
        self.device = Device(
            ip="10.0.0.50",
            sector_id=sector.id,
            hostname="pc-lab",
            last_status=DeviceStatus.UNKNOWN,
        )
        db.session.add(self.device)
        db.session.commit()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _history_count(self) -> int:
        return int(
            db.session.scalar(select(func.count()).select_from(DeviceHistory)) or 0
        )

    @patch("app.services.ping_service.ping_host")
    def test_check_online_writes_history_and_updates_status(self, mock_ping) -> None:
        mock_ping.return_value = PingResult(DeviceStatus.ONLINE, 12, "ok")
        payload = check_device(self.device)
        db.session.refresh(self.device)

        self.assertEqual(payload["status"], DeviceStatus.ONLINE)
        self.assertEqual(payload["previous_status"], DeviceStatus.UNKNOWN)
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["response_time_ms"], 12)
        self.assertEqual(self.device.last_status, DeviceStatus.ONLINE)
        self.assertEqual(self.device.last_response_time_ms, 12)
        self.assertIsNotNone(self.device.last_seen)
        self.assertEqual(self._history_count(), 1)

        row = db.session.scalars(select(DeviceHistory)).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.status, DeviceStatus.ONLINE)
        self.assertEqual(row.response_time_ms, 12)

    @patch("app.services.ping_service.ping_host")
    def test_check_offline_unchanged_still_logs_history(self, mock_ping) -> None:
        self.device.last_status = DeviceStatus.OFFLINE
        db.session.commit()
        mock_ping.return_value = PingResult(DeviceStatus.OFFLINE, None, "no reply")

        payload = check_device(self.device)
        db.session.refresh(self.device)

        self.assertEqual(payload["status"], DeviceStatus.OFFLINE)
        self.assertFalse(payload["changed"])
        self.assertEqual(self.device.last_status, DeviceStatus.OFFLINE)
        self.assertIsNone(self.device.last_response_time_ms)
        self.assertEqual(self._history_count(), 1)


if __name__ == "__main__":
    unittest.main()
