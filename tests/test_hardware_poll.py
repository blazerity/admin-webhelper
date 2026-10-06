"""Опрос железа Windows: разбор ОС/ГиБ, выбор целей, запись снимка.

Запуск: python -m unittest tests.test_hardware_poll
Нужен Flask/SQLAlchemy (pip install -r requirements.txt). SQLite in-memory.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from app import create_app
from app.extensions import db
from app.models import Device, DeviceHardwareHistory, DeviceStatus, HardwarePollRun, Sector
from app.schema import ensure_schema
from app.services.discovery_service import DiscoveryCredentials, WmiHardware
from app.services.hardware_info import (
    bytes_to_gb,
    build_hardware_snapshot,
    format_os_label,
    map_build_to_display,
    normalize_stored_capacity_gb,
    parse_windows_caption,
)
from app.services.hardware_poll_service import (
    HardwarePollError,
    apply_hardware_snapshot,
    is_windows_hardware_target,
    resolve_hardware_owner,
    run_hardware_poll,
)
from app.services.hardware_poll_settings import (
    DEFAULT_SCHEDULE_CRON,
    get_hardware_poll_settings,
    set_hardware_poll_settings,
)
from app.services.ping_service import PingResult


def _device(
    sector_id: int,
    ip: str,
    *,
    hostname: str | None = None,
    serial: str | None = None,
    fingerprint: str | None = None,
) -> Device:
    return Device(
        ip=ip,
        hostname=hostname,
        serial_number=serial,
        fingerprint_kind=fingerprint,
        sector_id=sector_id,
        last_status=DeviceStatus.ONLINE,
    )


class HardwareInfoTests(unittest.TestCase):
    def test_bytes_to_gb_ram_nominal(self) -> None:
        # Точные планки и «рваный» TotalPhysicalMemory (резерв BIOS).
        self.assertEqual(bytes_to_gb(16 * 1024**3, kind="ram"), 16)
        self.assertEqual(bytes_to_gb(17179869184, kind="ram"), 16)
        self.assertEqual(bytes_to_gb(int(15.2 * 1024**3), kind="ram"), 16)
        self.assertEqual(bytes_to_gb(15 * 1024**3, kind="ram"), 16)
        self.assertEqual(bytes_to_gb(0, kind="ram"), 0)
        self.assertIsNone(bytes_to_gb(None, kind="ram"))

    def test_bytes_to_gb_disk_label(self) -> None:
        # Этикетка SSD: Size ≈ N·1000³, не N·1024³ (иначе 238/244 вместо 256).
        self.assertEqual(bytes_to_gb(256 * 1000**3, kind="disk"), 256)
        self.assertEqual(bytes_to_gb(256060514304, kind="disk"), 256)
        self.assertEqual(bytes_to_gb(262144000000, kind="disk"), 256)
        self.assertEqual(bytes_to_gb(512 * 1000**3, kind="disk"), 512)
        self.assertEqual(bytes_to_gb(512 * 1024**2, kind="disk"), 1)
        self.assertEqual(bytes_to_gb(0, kind="disk"), 0)
        self.assertIsNone(bytes_to_gb(None))

    def test_normalize_stored_capacity_from_v150(self) -> None:
        # То, что уже лежит в БД после опроса на round(ГиБ).
        self.assertEqual(normalize_stored_capacity_gb(15, kind="ram"), 16)
        self.assertEqual(normalize_stored_capacity_gb(16, kind="ram"), 16)
        self.assertEqual(normalize_stored_capacity_gb(238, kind="disk"), 256)
        self.assertEqual(normalize_stored_capacity_gb(244, kind="disk"), 256)
        self.assertEqual(normalize_stored_capacity_gb(476, kind="disk"), 512)
        self.assertEqual(normalize_stored_capacity_gb(465, kind="disk"), 500)
        self.assertIsNone(normalize_stored_capacity_gb(None, kind="disk"))

    def test_parse_windows_11_pro_25h2(self) -> None:
        snap = build_hardware_snapshot(
            cpu_name="  Intel(R)  Core(TM) i7-10700  CPU @ 2.90GHz ",
            ram_bytes=16 * 1024**3,
            disk_bytes=512 * 1000**3,
            os_caption="Microsoft Windows 11 Pro",
            os_version="10.0.26100",
            os_build="26100",
            os_display_version="25h2",
            os_edition_id="Professional",
        )
        self.assertEqual(snap.os_family, "Windows 11")
        self.assertEqual(snap.os_edition, "Pro")
        self.assertEqual(snap.os_display_version, "25H2")
        self.assertEqual(snap.os_build, "26100")
        self.assertEqual(snap.ram_gb, 16)
        self.assertEqual(snap.disk_gb, 512)
        self.assertEqual(snap.os_label, "Windows 11 Pro 25H2")
        self.assertIn("i7-10700", snap.cpu_name or "")

    def test_parse_windows_11_enterprise_26h2_from_build(self) -> None:
        family, edition = parse_windows_caption("Microsoft Windows 11 Enterprise")
        self.assertEqual(family, "Windows 11")
        self.assertEqual(edition, "Enterprise")
        self.assertEqual(map_build_to_display(28000, family="Windows 11"), "26H2")
        self.assertEqual(
            format_os_label("Windows 11", "Enterprise", "26H2"),
            "Windows 11 Enterprise 26H2",
        )

    def test_parse_windows_10_and_server(self) -> None:
        self.assertEqual(
            parse_windows_caption("Microsoft Windows 10 Enterprise"),
            ("Windows 10", "Enterprise"),
        )
        family, edition = parse_windows_caption(
            "Microsoft Windows Server 2022 Datacenter"
        )
        self.assertEqual(family, "Windows Server 2022")
        self.assertEqual(edition, "Datacenter")
        snap = build_hardware_snapshot(
            os_caption="Microsoft Windows Server 2022 Datacenter",
            os_build="20348",
            os_display_version="21H2",
            os_edition_id="ServerDatacenter",
        )
        self.assertEqual(snap.os_label, "Windows Server 2022 Datacenter")
        self.assertIsNone(map_build_to_display(20348, family=snap.os_family))

    def test_build_26100_without_registry_is_24h2(self) -> None:
        snap = build_hardware_snapshot(
            os_caption="Microsoft Windows 11 Pro",
            os_build="26100",
        )
        self.assertEqual(snap.os_display_version, "24H2")


class HardwarePollServiceTests(unittest.TestCase):
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

    def test_schema_creates_hardware_tables_and_device_columns(self) -> None:
        self.assertTrue(db.inspect(db.engine).has_table("hardware_poll_runs"))
        self.assertTrue(db.inspect(db.engine).has_table("device_hardware_history"))
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("devices")}
        self.assertTrue(
            {"cpu_name", "ram_gb", "disk_gb", "os_family", "os_edition", "os_display_version"}.issubset(cols)
        )

    def test_ensure_schema_migrates_stored_ram_disk_nominals(self) -> None:
        from app.models import AppSetting
        from app.schema import HARDWARE_GB_NOMINAL_FIX_KEY
        from app.utils import utcnow

        device = _device(self.sector.id, "10.0.0.40", hostname="n179", serial="SNMIG")
        device.ram_gb = 15
        device.disk_gb = 244
        db.session.add(device)
        db.session.flush()
        history = DeviceHardwareHistory(
            device_id=device.id,
            collected_at=utcnow(),
            cpu_name="Intel Core i5",
            ram_gb=15,
            disk_gb=238,
            os_family="Windows 11",
            os_edition="Pro",
            os_display_version="25H2",
            os_build="26100",
        )
        db.session.add(history)
        marker = db.session.get(AppSetting, HARDWARE_GB_NOMINAL_FIX_KEY)
        if marker is not None:
            db.session.delete(marker)
        db.session.commit()

        ensure_schema()
        db.session.refresh(device)
        db.session.refresh(history)
        self.assertEqual(device.ram_gb, 16)
        self.assertEqual(device.disk_gb, 256)
        self.assertEqual(history.ram_gb, 16)
        self.assertEqual(history.disk_gb, 256)
        self.assertEqual(
            db.session.get(AppSetting, HARDWARE_GB_NOMINAL_FIX_KEY).value,
            "1",
        )

        # Повторный старт не трогает уже прогнанную БД.
        device.ram_gb = 15
        db.session.commit()
        ensure_schema()
        db.session.refresh(device)
        self.assertEqual(device.ram_gb, 15)

    def test_windows_targets_serial_and_naming_not_cameras(self) -> None:
        notebook = _device(self.sector.id, "10.0.0.1", hostname="n179", serial="ABC123")
        named = _device(self.sector.id, "10.0.0.2", hostname="w11471")
        server = _device(self.sector.id, "10.0.0.3", hostname="ktn-dc01")
        camera = _device(self.sector.id, "10.0.0.4", hostname="cam-01")
        printer = _device(self.sector.id, "10.0.0.5", hostname="mfp-office")
        grey_win = _device(self.sector.id, "10.0.0.6", fingerprint="windows")
        db.session.add_all([notebook, named, server, camera, printer, grey_win])
        db.session.commit()
        self.assertTrue(is_windows_hardware_target(notebook))
        self.assertTrue(is_windows_hardware_target(named))
        self.assertTrue(is_windows_hardware_target(server))
        self.assertTrue(is_windows_hardware_target(grey_win))
        self.assertFalse(is_windows_hardware_target(camera))
        self.assertFalse(is_windows_hardware_target(printer))

    def test_apply_snapshot_writes_history_only_on_change(self) -> None:
        device = _device(self.sector.id, "10.0.0.10", hostname="n179", serial="SN1")
        db.session.add(device)
        db.session.commit()
        first = build_hardware_snapshot(
            cpu_name="Intel Core i7-10700",
            ram_bytes=16 * 1024**3,
            disk_bytes=512 * 1024**3,
            os_caption="Microsoft Windows 11 Pro",
            os_display_version="25H2",
            os_build="26100",
        )
        self.assertTrue(apply_hardware_snapshot(device, first))
        db.session.commit()
        self.assertEqual(device.os_label, "Windows 11 Pro 25H2")
        self.assertEqual(
            db.session.scalar(select(func.count()).select_from(DeviceHardwareHistory)),
            1,
        )
        self.assertFalse(apply_hardware_snapshot(device, first))
        db.session.commit()
        self.assertEqual(
            db.session.scalar(select(func.count()).select_from(DeviceHardwareHistory)),
            1,
        )
        upgraded = build_hardware_snapshot(
            cpu_name=first.cpu_name,
            ram_bytes=32 * 1024**3,
            disk_bytes=512 * 1024**3,
            os_caption="Microsoft Windows 11 Pro",
            os_display_version="26H2",
            os_build="28000",
        )
        self.assertTrue(apply_hardware_snapshot(device, upgraded))
        db.session.commit()
        self.assertEqual(device.ram_gb, 32)
        self.assertEqual(device.os_display_version, "26H2")
        self.assertEqual(
            db.session.scalar(select(func.count()).select_from(DeviceHardwareHistory)),
            2,
        )

    def test_run_skips_camera_and_writes_windows(self) -> None:
        win = _device(self.sector.id, "10.10.0.1", hostname="n179", serial="SNWIN")
        cam = _device(self.sector.id, "10.10.0.2", hostname="cam-hall")
        db.session.add_all([win, cam])
        db.session.commit()

        def fake_ping(ip: str, *args, **kwargs):
            return PingResult(DeviceStatus.ONLINE, 1, "ok")

        def fake_wmi(ip: str, creds=None):
            return WmiHardware(
                cpu_name="Intel Core i5-10400",
                ram_bytes=8 * 1024**3,
                disk_bytes=256 * 1024**3,
                os_caption="Microsoft Windows 11 Enterprise",
                os_version="10.0.26100",
                os_build="26100",
                os_display_version="25H2",
                os_edition_id="Enterprise",
                serial_number="SNWIN",
                hostname="n179",
            )

        with (
            patch(
                "app.services.hardware_poll_service.discovery_service.discovery_credentials",
                return_value=DiscoveryCredentials("u", "p", "corp"),
            ),
            patch(
                "app.services.hardware_poll_service.ping_host",
                side_effect=fake_ping,
            ),
            patch(
                "app.services.hardware_poll_service.discovery_service.lookup_wmi_hardware",
                side_effect=fake_wmi,
            ),
        ):
            result = run_hardware_poll(mode="cli")

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.collected, 1)
        self.assertEqual(result.changed, 1)
        db.session.refresh(win)
        db.session.refresh(cam)
        self.assertEqual(win.os_family, "Windows 11")
        self.assertEqual(win.os_edition, "Enterprise")
        self.assertEqual(win.ram_gb, 8)
        self.assertIsNone(cam.cpu_name)
        self.assertEqual(
            db.session.scalar(select(func.count()).select_from(HardwarePollRun)),
            1,
        )

    def test_resolve_owner_rejects_foreign_serial_on_same_ip(self) -> None:
        card = _device(self.sector.id, "10.254.240.10", hostname="n13888", serial="ULTRA7")
        db.session.add(card)
        db.session.commit()
        self.assertIsNone(
            resolve_hardware_owner(
                card, live_serial="I5SN", live_hostname="n14001", allow_remap=False
            )
        )
        self.assertIs(
            resolve_hardware_owner(
                card, live_serial="ULTRA7", live_hostname="n13888"
            ),
            card,
        )

    def test_resolve_owner_hostname_fallback_when_serial_missing(self) -> None:
        card = _device(self.sector.id, "10.254.240.10", hostname="n13888.stepcon.ru", serial="ULTRA7")
        db.session.add(card)
        db.session.commit()
        self.assertIs(
            resolve_hardware_owner(card, live_serial=None, live_hostname="n13888"),
            card,
        )
        self.assertIsNone(
            resolve_hardware_owner(card, live_serial=None, live_hostname="n14001")
        )

    def test_run_does_not_mix_hardware_when_vpn_ip_reused(self) -> None:
        """Короткий VPN-lease: на старом IP другой ПК — карточку не переписываем."""
        notebook = _device(
            self.sector.id, "10.254.240.10", hostname="n13888", serial="ULTRA7"
        )
        notebook.cpu_name = "Intel(R) Core(TM) Ultra 7 268V"
        notebook.ram_gb = 32
        notebook.disk_gb = 2048
        notebook.os_family = "Windows 11"
        notebook.os_edition = "Pro"
        notebook.os_display_version = "25H2"
        notebook.os_build = "26200"
        from app.utils import utcnow

        notebook.hardware_checked_at = utcnow()
        db.session.add(notebook)
        db.session.commit()
        history = DeviceHardwareHistory(
            device_id=notebook.id,
            collected_at=notebook.hardware_checked_at,
            cpu_name=notebook.cpu_name,
            ram_gb=32,
            disk_gb=2048,
            os_family="Windows 11",
            os_edition="Pro",
            os_display_version="25H2",
            os_build="26200",
        )
        db.session.add(history)
        db.session.commit()

        def fake_ping(ip: str, *args, **kwargs):
            return PingResult(DeviceStatus.ONLINE, 1, "ok")

        def fake_wmi(ip: str, creds=None):
            return WmiHardware(
                cpu_name="11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz",
                ram_bytes=8 * 1024**3,
                disk_bytes=512 * 1000**3,
                os_caption="Microsoft Windows 10 Pro",
                os_version="10.0.19045",
                os_build="19045",
                os_display_version="22H2",
                os_edition_id="Professional",
                serial_number="I5SN",
                hostname="n14001",
            )

        with (
            patch(
                "app.services.hardware_poll_service.discovery_service.discovery_credentials",
                return_value=DiscoveryCredentials("u", "p", "corp"),
            ),
            patch(
                "app.services.hardware_poll_service.ping_host",
                side_effect=fake_ping,
            ),
            patch(
                "app.services.hardware_poll_service.discovery_service.lookup_wmi_hardware",
                side_effect=fake_wmi,
            ),
        ):
            result = run_hardware_poll(mode="cli")

        db.session.refresh(notebook)
        self.assertEqual(result.online, 1)
        self.assertEqual(result.collected, 0)
        self.assertEqual(result.mismatched, 1)
        self.assertEqual(notebook.cpu_name, "Intel(R) Core(TM) Ultra 7 268V")
        self.assertEqual(notebook.ram_gb, 32)
        self.assertEqual(notebook.os_family, "Windows 11")
        self.assertEqual(
            db.session.scalar(select(func.count()).select_from(DeviceHardwareHistory)),
            1,
        )

    def test_run_writes_snapshot_to_serial_owner_not_stale_ip_card(self) -> None:
        stale = _device(self.sector.id, "10.254.240.10", hostname="n13888", serial="ULTRA7")
        owner = _device(self.sector.id, "10.254.240.99", hostname="n14001", serial="I5SN")
        db.session.add_all([stale, owner])
        db.session.commit()

        def fake_ping(ip: str, *args, **kwargs):
            return PingResult(DeviceStatus.ONLINE, 1, "ok")

        def fake_wmi(ip: str, creds=None):
            return WmiHardware(
                cpu_name="11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz",
                ram_bytes=8 * 1024**3,
                disk_bytes=512 * 1000**3,
                os_caption="Microsoft Windows 10 Pro",
                os_build="19045",
                os_display_version="22H2",
                os_edition_id="Professional",
                serial_number="I5SN",
                hostname="n14001",
            )

        with (
            patch(
                "app.services.hardware_poll_service.discovery_service.discovery_credentials",
                return_value=DiscoveryCredentials("u", "p", "corp"),
            ),
            patch(
                "app.services.hardware_poll_service.ping_host",
                side_effect=fake_ping,
            ),
            patch(
                "app.services.hardware_poll_service.discovery_service.lookup_wmi_hardware",
                side_effect=fake_wmi,
            ),
        ):
            result = run_hardware_poll(mode="cli")

        db.session.refresh(stale)
        db.session.refresh(owner)
        self.assertGreaterEqual(result.collected, 1)
        self.assertIsNone(stale.cpu_name)
        self.assertIn("i5-1135G7", owner.cpu_name or "")
        self.assertEqual(owner.ram_gb, 8)
        self.assertEqual(owner.os_family, "Windows 10")

    def test_targeted_poll_does_not_remap_to_other_card(self) -> None:
        stale = _device(self.sector.id, "10.254.240.10", hostname="n13888", serial="ULTRA7")
        owner = _device(self.sector.id, "10.254.240.99", hostname="n14001", serial="I5SN")
        db.session.add_all([stale, owner])
        db.session.commit()

        def fake_wmi(ip: str, creds=None):
            return WmiHardware(
                cpu_name="Intel Core i5-1135G7",
                ram_bytes=8 * 1024**3,
                disk_bytes=512 * 1000**3,
                os_caption="Microsoft Windows 10 Pro",
                os_build="19045",
                serial_number="I5SN",
                hostname="n14001",
            )

        with (
            patch(
                "app.services.hardware_poll_service.discovery_service.discovery_credentials",
                return_value=DiscoveryCredentials("u", "p", "corp"),
            ),
            patch(
                "app.services.hardware_poll_service.ping_host",
                return_value=PingResult(DeviceStatus.ONLINE, 1, "ok"),
            ),
            patch(
                "app.services.hardware_poll_service.discovery_service.lookup_wmi_hardware",
                side_effect=fake_wmi,
            ),
        ):
            result = run_hardware_poll(mode="manual", device_ids=(stale.id,))

        db.session.refresh(stale)
        db.session.refresh(owner)
        self.assertEqual(result.collected, 0)
        self.assertEqual(result.mismatched, 1)
        self.assertIsNone(stale.cpu_name)
        self.assertIsNone(owner.cpu_name)

    def test_missing_creds_raises(self) -> None:
        with patch(
            "app.services.hardware_poll_service.discovery_service.discovery_credentials",
            return_value=None,
        ):
            with self.assertRaises(HardwarePollError):
                run_hardware_poll(mode="cli")

    def test_settings_default_noon_and_cron_validation(self) -> None:
        settings = get_hardware_poll_settings()
        self.assertTrue(settings.schedule_enabled)
        self.assertEqual(settings.schedule_cron, DEFAULT_SCHEDULE_CRON)
        saved = set_hardware_poll_settings(schedule_cron="30 12 * * 1-5")
        self.assertEqual(saved.schedule_cron, "30 12 * * 1-5")
        with self.assertRaises(ValueError):
            set_hardware_poll_settings(schedule_cron="noon")


class HostnameSweepIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app("testing")

    def setUp(self) -> None:
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        ensure_schema()
        self.sector = Sector(name="vpn", description="")
        db.session.add(self.sector)
        db.session.commit()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_sweep_skips_foreign_serial_on_reused_ip(self) -> None:
        from app.services.discovery_service import WmiInventory
        from app.services.hostname_sweep_service import run_hostname_sweep

        device = _device(self.sector.id, "10.254.240.10", hostname="n13888", serial="ULTRA7")
        db.session.add(device)
        db.session.commit()

        with (
            patch(
                "app.services.hostname_sweep_service.discovery_service.discovery_credentials",
                return_value=DiscoveryCredentials("u", "p", "corp"),
            ),
            patch(
                "app.services.hostname_sweep_service.ping_host",
                return_value=PingResult(DeviceStatus.ONLINE, 1, "ok"),
            ),
            patch(
                "app.services.hostname_sweep_service.discovery_service.lookup_hostname",
                return_value=None,
            ),
            patch(
                "app.services.hostname_sweep_service.discovery_service.lookup_wmi_inventory",
                return_value=WmiInventory(
                    serial_number="I5SN", hostname="n14001.stepcon.ru"
                ),
            ),
        ):
            result = run_hostname_sweep()

        db.session.refresh(device)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(device.hostname, "n13888")


if __name__ == "__main__":
    unittest.main()
