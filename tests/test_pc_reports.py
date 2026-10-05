"""Отчёты о ПК: ноутбуки/СБ, железо, кандидаты, пробелы, CSV.

Запуск: python -m unittest tests.test_pc_reports
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from app import create_app
from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, Sector
from app.schema import ensure_schema
from app.services.sector_daily_report_service import (
    INVENTORY_STALE_DAYS,
    build_sector_daily_report,
    charts_payload,
    previous_calendar_day,
    report_to_csv,
)
from app.utils import utcnow


def _sector(name: str) -> Sector:
    sector = Sector(name=name)
    db.session.add(sector)
    db.session.flush()
    return sector


def _pc(
    sector_id: int,
    ip: str,
    *,
    hostname: str,
    serial: str | None = None,
    ram_gb: int | None = None,
    disk_gb: int | None = None,
    cpu_name: str | None = None,
    os_family: str | None = None,
    os_edition: str | None = None,
    os_display_version: str | None = None,
    hardware_checked_at=None,
) -> Device:
    device = Device(
        ip=ip,
        hostname=hostname,
        serial_number=serial,
        sector_id=sector_id,
        last_status=DeviceStatus.ONLINE,
        ram_gb=ram_gb,
        disk_gb=disk_gb,
        cpu_name=cpu_name,
        os_family=os_family,
        os_edition=os_edition,
        os_display_version=os_display_version,
        hardware_checked_at=hardware_checked_at,
    )
    db.session.add(device)
    db.session.flush()
    return device


class PcReportsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app("testing")

    def setUp(self) -> None:
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        ensure_schema()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_counts_notebooks_and_desktops_by_sector(self) -> None:
        a = _sector("АХО")
        b = _sector("Бухгалтерия")
        now = utcnow()
        _pc(
            a.id,
            "10.0.0.1",
            hostname="n101",
            serial="SN-N1",
            ram_gb=16,
            disk_gb=512,
            cpu_name="Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz",
            os_family="Windows 11",
            os_edition="Pro",
            os_display_version="24H2",
            hardware_checked_at=now,
        )
        _pc(
            a.id,
            "10.0.0.2",
            hostname="w102",
            serial="SN-W1",
            ram_gb=8,
            disk_gb=256,
            cpu_name="Intel(R) Core(TM) i3-6100 CPU @ 3.70GHz",
            os_family="Windows 10",
            os_edition="Pro",
            os_display_version="22H2",
            hardware_checked_at=now,
        )
        _pc(b.id, "10.0.1.1", hostname="n201", serial="SN-N2")  # без железа
        _pc(a.id, "10.0.0.9", hostname="cam-01", serial=None)  # не ПК
        db.session.commit()

        report = build_sector_daily_report(source="test")
        self.assertEqual(report.notebook_total, 2)
        self.assertEqual(report.desktop_total, 1)
        self.assertEqual(report.pc_total, 3)
        self.assertEqual(report.with_hardware_total, 2)
        self.assertEqual(report.without_hardware_total, 1)

        by_name = {row.sector_name: row for row in report.sectors}
        self.assertEqual(by_name["АХО"].notebook_count, 1)
        self.assertEqual(by_name["АХО"].desktop_count, 1)
        self.assertEqual(by_name["Бухгалтерия"].notebook_count, 1)

        self.assertEqual(report.kind_chart.values, [2, 1])
        self.assertIn("5–8 ГБ", report.ram_chart.labels)
        self.assertTrue(report.cpu_chart.labels)
        self.assertGreaterEqual(report.replacement_total, 1)
        self.assertGreaterEqual(report.inventory_gap_total, 1)

        payload = charts_payload(report)
        self.assertEqual(payload["kind"]["values"], [2, 1])
        csv_text = report_to_csv(report)
        self.assertIn("АХО", csv_text)
        self.assertIn("Кандидаты на замену", csv_text)

    def test_active_pc_from_history_yesterday(self) -> None:
        sector = _sector("Сеть")
        notebook = _pc(sector.id, "10.1.0.1", hostname="n301", serial="SN-A")
        desktop = _pc(sector.id, "10.1.0.2", hostname="w302", serial="SN-B")
        day = previous_calendar_day()
        # Вчера online только ноутбук.
        from datetime import datetime, time

        local = datetime.now().astimezone().tzinfo
        stamp = datetime.combine(day, time(12, 0), tzinfo=local).astimezone(
            __import__("datetime").timezone.utc
        )
        db.session.add(
            DeviceHistory(
                device_id=notebook.id,
                status=DeviceStatus.ONLINE,
                response_time_ms=12,
                timestamp=stamp,
            )
        )
        db.session.add(
            DeviceHistory(
                device_id=desktop.id,
                status=DeviceStatus.OFFLINE,
                response_time_ms=None,
                timestamp=stamp,
            )
        )
        db.session.commit()

        report = build_sector_daily_report(report_date=day, source="test")
        self.assertEqual(report.active_pc_total, 1)
        self.assertEqual(report.sectors[0].active_notebook, 1)
        self.assertEqual(report.sectors[0].active_desktop, 0)

    def test_stale_hardware_is_inventory_gap(self) -> None:
        sector = _sector("Склад")
        stale = utcnow() - timedelta(days=INVENTORY_STALE_DAYS + 3)
        _pc(
            sector.id,
            "10.2.0.1",
            hostname="n401",
            serial="SN-OLD",
            ram_gb=32,
            disk_gb=1024,
            cpu_name="AMD Ryzen 5 5600G",
            os_family="Windows 11",
            os_edition="Pro",
            os_display_version="24H2",
            hardware_checked_at=stale,
        )
        db.session.commit()
        report = build_sector_daily_report(source="test")
        self.assertEqual(report.inventory_gap_total, 1)
        self.assertIn("старше", report.inventory_gaps[0].reason)


if __name__ == "__main__":
    unittest.main()
