"""Имя, MAC и серийник. Сокет и subprocess подменены, пакетов в сеть нет."""

import socket
from types import SimpleNamespace

import pytest

from app.services.discovery_service import (
    WmiInventory,
    hostname_key,
    hostnames_match,
    lookup_hostname,
    lookup_mac,
    lookup_serial,
    lookup_wmi_inventory,
    normalize_serial,
)
from app.services.net_utils import NetworkInputError


def test_lookup_mac_parses_neighbor_line(monkeypatch):
    def fake_run(argv, **kwargs):
        assert kwargs["shell"] is not True
        assert "10.0.0.5" in argv
        return SimpleNamespace(
            returncode=0,
            stdout="10.0.0.5 ether aa:bb:cc:dd:ee:ff lladdr\n",
            stderr="",
        )

    monkeypatch.setattr("app.services.discovery_service.subprocess.run", fake_run)
    assert lookup_mac("10.0.0.5") == "AA:BB:CC:DD:EE:FF"


def test_lookup_mac_rejects_shell_payload(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise AssertionError("subprocess не должен запускаться")

    monkeypatch.setattr("app.services.discovery_service.subprocess.run", fake_run)
    with pytest.raises(NetworkInputError):
        lookup_mac("1.2.3.4;whoami")


def test_lookup_hostname_strips_trailing_dot(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.socket.gethostbyaddr",
        lambda ip: ("pc.example.", [], [ip]),
    )
    assert lookup_hostname("10.0.0.5") == "pc.example"


def test_lookup_hostname_restores_timeout_on_oserror(monkeypatch):
    socket.setdefaulttimeout(7)

    def boom(_ip):
        raise OSError("нет PTR")

    monkeypatch.setattr("app.services.discovery_service.socket.gethostbyaddr", boom)
    try:
        assert lookup_hostname("10.0.0.5") is None
        assert socket.getdefaulttimeout() == 7
    finally:
        socket.setdefaulttimeout(None)


def test_normalize_serial_rejects_oem_placeholders():
    assert normalize_serial("  abc1234  ") == "ABC1234"
    assert normalize_serial("To Be Filled By O.E.M.") is None
    assert normalize_serial("Default string") is None
    assert normalize_serial("0") is None
    assert normalize_serial("") is None


def test_hostname_key_strips_domain():
    assert hostname_key("N14002.example.com.") == "n14002"
    assert hostname_key("n14002") == "n14002"
    assert hostnames_match("n14002.corp.local", "N14002")
    assert not hostnames_match("n14002", "n14003")


def test_lookup_wmi_inventory_skips_without_credentials(app, monkeypatch):
    app.config["DISCOVERY_USERNAME"] = ""
    app.config["DISCOVERY_PASSWORD"] = ""

    def boom(*_args, **_kwargs):
        raise AssertionError("WMI не должен вызываться без учётки")

    monkeypatch.setattr("app.services.discovery_service._wmi_inventory", boom)
    assert lookup_wmi_inventory("10.0.0.5") == WmiInventory()
    assert lookup_serial("10.0.0.5") is None


def test_lookup_wmi_inventory_returns_serial_and_mac(app, monkeypatch):
    app.config["DISCOVERY_USERNAME"] = "svc"
    app.config["DISCOVERY_PASSWORD"] = "secret"
    app.config["DISCOVERY_DOMAIN"] = "CORP"
    monkeypatch.setattr(
        "app.services.discovery_service._wmi_inventory",
        lambda ip, creds: WmiInventory(serial_number="DELLTAG9", mac="AA:BB:CC:DD:EE:FF"),
    )
    inventory = lookup_wmi_inventory("10.0.0.5")
    assert inventory.serial_number == "DELLTAG9"
    assert inventory.mac == "AA:BB:CC:DD:EE:FF"
    assert lookup_serial("10.0.0.5") == "DELLTAG9"


def test_wmi_mac_prefers_adapter_with_matching_ip(monkeypatch):
    from app.services import discovery_service as ds

    class FakeEnum:
        def __init__(self, rows):
            self._rows = list(rows)

        def Next(self, *_args):
            if not self._rows:
                raise Exception("S_FALSE")
            return [self._rows.pop(0)]

        def RemRelease(self):
            return None

    class FakeItem:
        def __init__(self, props):
            self._props = props

        def getProperties(self):
            return self._props

    rows = [
        FakeItem(
            {
                "MACAddress": {"value": "11:22:33:44:55:66"},
                "IPAddress": {"value": ["10.0.0.9", "fe80::1"]},
            }
        ),
        FakeItem(
            {
                "MACAddress": {"value": "aa:bb:cc:dd:ee:ff"},
                "IPAddress": {"value": ["10.0.0.5"]},
            }
        ),
    ]

    class FakeServices:
        def ExecQuery(self, query):
            assert "Win32_NetworkAdapterConfiguration" in query
            return FakeEnum(rows)

    assert ds._wmi_query_mac(FakeServices(), "10.0.0.5") == "AA:BB:CC:DD:EE:FF"


def test_wmi_mac_falls_back_to_first_enabled(monkeypatch):
    from app.services import discovery_service as ds

    class FakeEnum:
        def __init__(self, rows):
            self._rows = list(rows)

        def Next(self, *_args):
            if not self._rows:
                raise Exception("S_FALSE")
            return [self._rows.pop(0)]

        def RemRelease(self):
            return None

    class FakeItem:
        def __init__(self, props):
            self._props = props

        def getProperties(self):
            return self._props

    rows = [
        FakeItem(
            {
                "MACAddress": {"value": "11-22-33-44-55-66"},
                "IPAddress": {"value": ["10.0.0.9"]},
            }
        ),
    ]

    class FakeServices:
        def ExecQuery(self, query):
            return FakeEnum(rows)

    assert ds._wmi_query_mac(FakeServices(), "10.0.0.5") == "11:22:33:44:55:66"
