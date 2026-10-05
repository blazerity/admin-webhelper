"""Идентичность устройств: только serial_number, hostname не ключ.

Запуск: python -m unittest tests.test_device_identity
Нужен Flask/SQLAlchemy (pip install -r requirements.txt). SQLite in-memory.
"""

from __future__ import annotations

import unittest

from sqlalchemy import func, select

from app import create_app
from app.extensions import db
from app.models import Device, DeviceStatus, Sector
from app.schema import ensure_schema
from app.services.ping_service import PingResult, _Probe, _save_online_probe


def _online(
    ip: str,
    *,
    hostname: str | None = None,
    serial: str | None = None,
    mac: str | None = None,
) -> _Probe:
    return _Probe(
        ip=ip,
        result=PingResult(DeviceStatus.ONLINE, 1, "ok"),
        hostname=hostname,
        mac=mac,
        serial_number=serial,
        hostname_from_wmi=bool(hostname),
    )


class DeviceIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app("testing")

    def setUp(self) -> None:
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        ensure_schema()
        self.sector = Sector(name="office", description="")
        db.session.add(self.sector)
        db.session.commit()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _save(self, probe: _Probe) -> None:
        self.assertTrue(_save_online_probe(probe, self.sector.id))

    def _all(self) -> list[Device]:
        return list(db.session.scalars(select(Device).order_by(Device.id)))

    def _count(self) -> int:
        return int(db.session.scalar(select(func.count()).select_from(Device)) or 0)

    def test_dhcp_ip_change_same_serial_updates_one_row(self) -> None:
        """Тот же SN на новом IP — одна карточка, адрес обновился."""
        self._save(
            _online(
                "10.10.20.10",
                hostname="n603.stepcon.ru",
                serial="4P246B4",
                mac="D4:A2:CD:83:45:36",
            )
        )
        self._save(
            _online(
                "10.254.240.133",
                hostname="n603.stepcon.ru",
                serial="4P246B4",
                mac="24:41:8C:CC:BD:D9",
            )
        )
        rows = self._all()
        self.assertEqual(len(rows), 1)
        device = rows[0]
        self.assertEqual(device.serial_number, "4P246B4")
        self.assertEqual(device.ip, "10.254.240.133")
        self.assertEqual(device.mac, "24:41:8C:CC:BD:D9")
        self.assertEqual(device.hostname, "n603.stepcon.ru")

    def test_same_hostname_different_serials_stay_two_rows(self) -> None:
        """Одинаковое имя при разных SN — две машины, не дубль одной."""
        self._save(
            _online("10.10.20.159", hostname="n603.stepcon.ru", serial="4P246B4")
        )
        self._save(
            _online("10.254.240.133", hostname="n603.stepcon.ru", serial="H3QYYV2")
        )
        self._save(
            _online("10.254.240.174", hostname="n603.stepcon.ru", serial="C237HG3")
        )
        rows = self._all()
        self.assertEqual(len(rows), 3)
        serials = {row.serial_number for row in rows}
        self.assertEqual(serials, {"4P246B4", "H3QYYV2", "C237HG3"})
        self.assertEqual({row.hostname for row in rows}, {"n603.stepcon.ru"})

    def test_hostname_only_does_not_merge_across_ips(self) -> None:
        """Одинаковое имя без SN на разных IP не склеивает записи."""
        self._save(_online("10.0.0.1", hostname="n603.stepcon.ru"))
        self._save(_online("10.0.0.2", hostname="n603.stepcon.ru"))
        rows = self._all()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.ip for row in rows}, {"10.0.0.1", "10.0.0.2"})
        self.assertTrue(all(row.serial_number is None for row in rows))

    def test_no_sn_probe_does_not_steal_row_with_serial(self) -> None:
        """Зонд без SN не забирает карточку ноутбука, даже на том же IP."""
        self._save(
            _online("10.10.20.10", hostname="n603.stepcon.ru", serial="4P246B4")
        )
        self._save(_online("10.10.20.10", hostname="n603.stepcon.ru"))
        rows = self._all()
        self.assertEqual(len(rows), 2)
        by_sn = {row.serial_number: row for row in rows}
        self.assertEqual(by_sn["4P246B4"].hostname, "n603.stepcon.ru")
        self.assertEqual(by_sn["4P246B4"].ip, "10.10.20.10")
        self.assertIsNone(by_sn[None].serial_number)

    def test_empty_sn_same_ip_then_serial_attaches(self) -> None:
        """Сначала без SN на IP, потом WMI на том же IP — тот же ряд, появился SN."""
        self._save(_online("10.10.20.10", hostname="n603.stepcon.ru"))
        first_id = self._all()[0].id
        self._save(
            _online("10.10.20.10", hostname="n603.stepcon.ru", serial="4P246B4")
        )
        rows = self._all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].serial_number, "4P246B4")

    def test_later_sn_on_new_ip_updates_existing_sn_row_ghost_remains(self) -> None:
        """Без SN на старом IP, затем SN на новом — SN-ряд живёт отдельно.

        Призрак без SN на старом адресе не сливается (дыра варианта 1).
        Повторный зонд с тем же SN на третьем IP обновляет SN-ряд.
        """
        self._save(_online("10.10.20.10", hostname="n603.stepcon.ru"))
        self._save(
            _online("10.254.240.1", hostname="n603.stepcon.ru", serial="4P246B4")
        )
        rows = self._all()
        self.assertEqual(len(rows), 2)
        ghost = next(row for row in rows if row.serial_number is None)
        sn_row = next(row for row in rows if row.serial_number == "4P246B4")
        self.assertEqual(ghost.ip, "10.10.20.10")
        self.assertEqual(sn_row.ip, "10.254.240.1")
        sn_id = sn_row.id

        self._save(
            _online("10.254.240.99", hostname="n603.stepcon.ru", serial="4P246B4")
        )
        rows = self._all()
        self.assertEqual(len(rows), 2)
        sn_row = db.session.get(Device, sn_id)
        ghost = db.session.get(Device, ghost.id)
        self.assertEqual(sn_row.ip, "10.254.240.99")
        self.assertEqual(ghost.ip, "10.10.20.10")
        self.assertIsNone(ghost.serial_number)

    def test_empty_sn_same_ip_reused(self) -> None:
        """Повторный онлайн без SN на том же IP не плодит строки."""
        self._save(_online("10.1.1.1", hostname="cam01"))
        self._save(_online("10.1.1.1", hostname="cam01"))
        self.assertEqual(self._count(), 1)


if __name__ == "__main__":
    unittest.main()
