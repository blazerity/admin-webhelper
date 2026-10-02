"""Пинг и один проход опроса. Сеть не используем: subprocess подменён."""

import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, Sector, SectorRange
from app.services.discovery_service import WmiInventory
from app.services.net_utils import NetworkInputError
from app.services.ping_service import PingResult, ping_host, poll_all_sectors, trace_host
from app.utils import utcnow


def test_ping_host_online_parses_time(monkeypatch):
    def fake_run(argv, **kwargs):
        assert kwargs["shell"] is False
        assert argv[-1] == "10.0.0.5"
        return SimpleNamespace(
            returncode=0,
            stdout="Reply from 10.0.0.5: bytes=32 time=12 ms TTL=64",
            stderr="",
        )

    monkeypatch.setattr("app.services.ping_service.subprocess.run", fake_run)
    result = ping_host("10.0.0.5")
    assert result.status == DeviceStatus.ONLINE
    assert result.response_time_ms == 12
    assert "time=12 ms" in result.output


def test_ping_host_parses_russian_and_less_than(monkeypatch):
    outputs = iter(
        [
            "Ответ от 10.0.0.5: число байт=32 время=12 мс TTL=128",
            "Ответ от 10.0.0.5: число байт=32 время<1мс TTL=128",
        ]
    )

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    monkeypatch.setattr("app.services.ping_service.subprocess.run", fake_run)
    assert ping_host("10.0.0.5").response_time_ms == 12
    assert ping_host("10.0.0.5").response_time_ms == 1


def test_ping_host_rejects_shell_payload(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise AssertionError("subprocess не должен запускаться для непроверенного IP")

    monkeypatch.setattr("app.services.ping_service.subprocess.run", fake_run)
    with pytest.raises(NetworkInputError):
        ping_host("1.2.3.4;whoami")


def test_trace_host_shell_is_not_true(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="1 10.0.0.5\n", stderr="")

    monkeypatch.setattr("app.services.ping_service.subprocess.run", fake_run)
    code, text = trace_host("10.0.0.5")
    assert code == 0
    assert "10.0.0.5" in text
    assert calls[0][1]["shell"] is not True
    assert calls[0][0][-1] == "10.0.0.5"


def _patch_discovery(
    monkeypatch, *, hostname="lab-pc.example", mac="AA:BB:CC:DD:EE:FF", serial="ABC1234"
):
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_hostname",
        lambda ip: hostname,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_mac",
        lambda ip: mac,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_wmi_inventory",
        lambda ip, creds=None: WmiInventory(serial_number=serial, mac=None),
    )


def test_poll_all_sectors_writes_device_and_history(app, monkeypatch):
    sector = Sector(name="lab", description="")
    sector.ranges.append(SectorRange(cidr="10.0.0.5"))
    db.session.add(sector)
    db.session.commit()

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: PingResult(DeviceStatus.ONLINE, 12, "time=12 ms"),
    )
    names = iter(["lab-pc.example", None])
    macs = iter(["AA:BB:CC:DD:EE:FF", None])
    inventories = iter(
        [
            WmiInventory(serial_number="ABC1234", mac=None),
            WmiInventory(),
        ]
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_hostname",
        lambda ip: next(names),
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_mac",
        lambda ip: next(macs),
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_wmi_inventory",
        lambda ip, creds=None: next(inventories),
    )

    stats = poll_all_sectors()
    assert stats == {"scanned": 1, "online": 1, "offline": 0, "errors": 0}

    device = db.session.scalar(select(Device).where(Device.ip == "10.0.0.5"))
    assert device is not None
    assert device.sector_id == sector.id
    assert device.last_status == DeviceStatus.ONLINE
    assert device.last_response_time_ms == 12
    assert device.last_seen is not None
    assert device.hostname == "lab-pc.example"
    assert device.mac == "AA:BB:CC:DD:EE:FF"
    assert device.serial_number == "ABC1234"

    history = db.session.scalars(
        select(DeviceHistory).where(DeviceHistory.device_id == device.id)
    ).all()
    assert len(history) == 1
    assert history[0].status == DeviceStatus.ONLINE
    assert history[0].response_time_ms == 12

    # Второй проход: lookup пустой, вчерашние имя, MAC и serial остаются.
    again = poll_all_sectors()
    assert again["online"] == 1
    db.session.refresh(device)
    assert device.hostname == "lab-pc.example"
    assert device.mac == "AA:BB:CC:DD:EE:FF"
    assert device.serial_number == "ABC1234"
    history = db.session.scalars(
        select(DeviceHistory).where(DeviceHistory.device_id == device.id)
    ).all()
    assert len(history) == 2


def test_poll_fills_mac_from_wmi_when_arp_empty(app, monkeypatch):
    sector = Sector(name="lab", description="")
    sector.ranges.append(SectorRange(cidr="10.0.0.5"))
    db.session.add(sector)
    db.session.commit()

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: PingResult(DeviceStatus.ONLINE, 9, "time=9 ms"),
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_hostname",
        lambda ip: "n14002",
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_mac",
        lambda ip: None,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_wmi_inventory",
        lambda ip, creds=None: WmiInventory(
            serial_number="DELLTAG9", mac="DE:AD:BE:EF:00:01"
        ),
    )

    stats = poll_all_sectors()
    assert stats["online"] == 1
    device = db.session.scalar(select(Device).where(Device.serial_number == "DELLTAG9"))
    assert device is not None
    assert device.mac == "DE:AD:BE:EF:00:01"
    assert device.hostname == "n14002"


def test_poll_passes_db_wmi_credentials_into_lookup(app, monkeypatch):
    """Учётка из Параметров читается в главном потоке и уходит в WMI-воркеры."""
    from app.services.discovery_service import DiscoveryCredentials
    from app.services.settings_service import save_discovery_credentials

    sector = Sector(name="lab", description="")
    sector.ranges.append(SectorRange(cidr="10.0.0.5"))
    db.session.add(sector)
    db.session.commit()
    save_discovery_credentials("wmisvc", "CORP", "WmiSecret!")

    seen: dict[str, DiscoveryCredentials | None] = {}

    def fake_lookup(ip, creds=None):
        seen["creds"] = creds
        return WmiInventory(serial_number="SN-FROM-WMI", mac="AA:BB:CC:DD:00:99")

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: PingResult(DeviceStatus.ONLINE, 5, "time=5 ms"),
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_hostname",
        lambda ip: "pc-wmi",
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_mac",
        lambda ip: None,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_wmi_inventory",
        fake_lookup,
    )

    stats = poll_all_sectors()
    assert stats["online"] == 1
    assert seen["creds"] is not None
    assert seen["creds"].username == "wmisvc"
    assert seen["creds"].domain == "CORP"
    assert seen["creds"].password == "WmiSecret!"
    device = db.session.scalar(select(Device).where(Device.serial_number == "SN-FROM-WMI"))
    assert device is not None
    assert device.mac == "AA:BB:CC:DD:00:99"


def test_probe_uses_passed_creds_without_app_context(app, monkeypatch):
    """Рабочий поток без Flask context всё равно получает заранее загруженную учётку."""
    from flask import has_app_context

    from app.services.discovery_service import DiscoveryCredentials
    from app.services.ping_service import _probe

    creds = DiscoveryCredentials(username="svc", password="secret", domain="CORP")
    seen = {}

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: PingResult(DeviceStatus.ONLINE, 3, "ok"),
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_hostname",
        lambda ip: "host1",
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_mac",
        lambda ip: None,
    )

    def fake_lookup(ip, creds=None):
        seen["has_context"] = has_app_context()
        seen["creds"] = creds
        return WmiInventory(serial_number="NOCTX1", mac="01:02:03:04:05:06")

    monkeypatch.setattr(
        "app.services.discovery_service.lookup_wmi_inventory",
        fake_lookup,
    )

    # Вызов как в ThreadPoolExecutor: снаружи app context.
    assert has_app_context()  # pytest держит контекст фикстуры app
    # Имитируем воркер: передаём creds явно; lookup не должен требовать БД.
    probe = _probe("10.0.0.5", creds)
    assert probe.serial_number == "NOCTX1"
    assert probe.mac == "01:02:03:04:05:06"
    assert seen["creds"] is creds


def test_poll_does_not_create_device_for_empty_ip(app, monkeypatch):
    sector = Sector(name="lab", description="")
    sector.ranges.append(SectorRange(cidr="10.0.0.5"))
    db.session.add(sector)
    db.session.commit()

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: PingResult(DeviceStatus.OFFLINE, None, "timeout"),
    )
    stats = poll_all_sectors()
    assert stats["scanned"] == 1
    assert stats["online"] == 0
    assert stats["offline"] == 0
    assert db.session.scalars(select(Device)).all() == []


def test_poll_keeps_history_when_ip_and_sector_change(app, monkeypatch):
    lab = Sector(name="lab", description="")
    lab.ranges.append(SectorRange(cidr="10.0.0.5"))
    office = Sector(name="office", description="")
    office.ranges.append(SectorRange(cidr="10.1.0.8"))
    db.session.add_all([lab, office])
    db.session.commit()

    answers = {
        "10.0.0.5": PingResult(DeviceStatus.ONLINE, 10, "time=10 ms"),
        "10.1.0.8": PingResult(DeviceStatus.OFFLINE, None, "down"),
    }

    def fake_ping(ip, timeout_s=1):
        return answers[ip]

    monkeypatch.setattr("app.services.ping_service.ping_host", fake_ping)
    _patch_discovery(monkeypatch, hostname="n14002.example", serial="DELLTAG1")

    first = poll_all_sectors()
    assert first["online"] == 1
    device = db.session.scalar(select(Device).where(Device.serial_number == "DELLTAG1"))
    assert device is not None
    assert device.ip == "10.0.0.5"
    assert device.sector_id == lab.id
    device_id = device.id

    # Ноутбук переехал: старый IP молчит, новый отвечает с тем же serial.
    answers = {
        "10.0.0.5": PingResult(DeviceStatus.OFFLINE, None, "down"),
        "10.1.0.8": PingResult(DeviceStatus.ONLINE, 8, "time=8 ms"),
    }
    second = poll_all_sectors()
    assert second["online"] == 1
    assert second["offline"] == 0

    devices = db.session.scalars(select(Device)).all()
    assert len(devices) == 1
    device = devices[0]
    assert device.id == device_id
    assert device.ip == "10.1.0.8"
    assert device.sector_id == office.id
    assert device.serial_number == "DELLTAG1"
    history = db.session.scalars(
        select(DeviceHistory).where(DeviceHistory.device_id == device_id)
    ).all()
    assert len(history) == 2
    assert {row.status for row in history} == {DeviceStatus.ONLINE}


def test_poll_matches_by_hostname_when_serial_missing(app, monkeypatch):
    sector = Sector(name="lab", description="")
    sector.ranges.append(SectorRange(cidr="10.0.0.5"))
    sector.ranges.append(SectorRange(cidr="10.0.0.6"))
    db.session.add(sector)
    db.session.commit()

    answers = {
        "10.0.0.5": PingResult(DeviceStatus.ONLINE, 5, "time=5 ms"),
        "10.0.0.6": PingResult(DeviceStatus.OFFLINE, None, "down"),
    }
    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: answers[ip],
    )
    _patch_discovery(monkeypatch, hostname="n14002.corp.local", serial=None)

    poll_all_sectors()
    device = db.session.scalar(select(Device).where(Device.hostname == "n14002.corp.local"))
    assert device is not None
    device_id = device.id

    answers = {
        "10.0.0.5": PingResult(DeviceStatus.OFFLINE, None, "down"),
        "10.0.0.6": PingResult(DeviceStatus.ONLINE, 7, "time=7 ms"),
    }
    # Короткое имя без суффикса — та же машина.
    _patch_discovery(monkeypatch, hostname="N14002", serial=None)
    poll_all_sectors()

    devices = db.session.scalars(select(Device)).all()
    assert len(devices) == 1
    assert devices[0].id == device_id
    assert devices[0].ip == "10.0.0.6"
    assert devices[0].hostname == "N14002"


def test_poll_marks_known_device_offline(app, monkeypatch):
    sector = Sector(name="lab", description="")
    sector.ranges.append(SectorRange(cidr="10.0.0.5"))
    db.session.add(sector)
    db.session.flush()
    device = Device(
        ip="10.0.0.5",
        hostname="n14002",
        serial_number="TAG1",
        sector_id=sector.id,
        last_status=DeviceStatus.ONLINE,
        last_seen=utcnow(),
    )
    db.session.add(device)
    db.session.commit()
    device_id = device.id

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: PingResult(DeviceStatus.OFFLINE, None, "down"),
    )
    stats = poll_all_sectors()
    assert stats["offline"] == 1
    db.session.refresh(device)
    assert device.id == device_id
    assert device.last_status == DeviceStatus.OFFLINE
    history = db.session.scalars(
        select(DeviceHistory).where(DeviceHistory.device_id == device_id)
    ).all()
    assert len(history) == 1
    assert history[0].status == DeviceStatus.OFFLINE


def test_poll_logs_overlap_as_summary(app, monkeypatch, caplog):
    """Пересекающиеся сектора — одна сводка, не строка на каждый IP."""
    first = Sector(name="a", description="")
    first.ranges.append(SectorRange(cidr="10.0.0.0/29"))
    second = Sector(name="b", description="")
    second.ranges.append(SectorRange(cidr="10.0.0.0/29"))
    db.session.add_all([first, second])
    db.session.commit()

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1: PingResult(DeviceStatus.OFFLINE, None, "down"),
    )
    with caplog.at_level(logging.WARNING, logger="app.services.ping_service"):
        poll_all_sectors()

    overlap_records = [
        record
        for record in caplog.records
        if "Пересечение диапазонов" in record.getMessage()
    ]
    assert len(overlap_records) == 1
    message = overlap_records[0].getMessage()
    assert "6 адресов" in message
    assert "входят в секторы" not in message
