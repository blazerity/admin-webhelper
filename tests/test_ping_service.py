"""Пинг и один проход опроса. Сеть не используем: subprocess подменён."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, Sector, SectorRange
from app.services.net_utils import NetworkInputError
from app.services.ping_service import PingResult, ping_host, poll_all_sectors, trace_host


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
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_hostname",
        lambda ip: next(names),
    )
    monkeypatch.setattr(
        "app.services.discovery_service.lookup_mac",
        lambda ip: next(macs),
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

    history = db.session.scalars(
        select(DeviceHistory).where(DeviceHistory.device_id == device.id)
    ).all()
    assert len(history) == 1
    assert history[0].status == DeviceStatus.ONLINE
    assert history[0].response_time_ms == 12

    # Второй проход: lookup пустой, вчерашние имя и MAC остаются.
    again = poll_all_sectors()
    assert again["online"] == 1
    db.session.refresh(device)
    assert device.hostname == "lab-pc.example"
    assert device.mac == "AA:BB:CC:DD:EE:FF"
    history = db.session.scalars(
        select(DeviceHistory).where(DeviceHistory.device_id == device.id)
    ).all()
    assert len(history) == 2
