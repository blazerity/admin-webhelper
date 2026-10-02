"""Имя, MAC и серийник. Сокет и subprocess подменены, пакетов в сеть нет."""

import socket
from types import SimpleNamespace

import pytest

from app.services.discovery_service import (
    hostname_key,
    hostnames_match,
    lookup_hostname,
    lookup_mac,
    lookup_serial,
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


def test_lookup_serial_skips_without_credentials(app, monkeypatch):
    app.config["DISCOVERY_USERNAME"] = ""
    app.config["DISCOVERY_PASSWORD"] = ""

    def boom(*_args, **_kwargs):
        raise AssertionError("WMI не должен вызываться без учётки")

    monkeypatch.setattr("app.services.discovery_service._wmi_bios_serial", boom)
    assert lookup_serial("10.0.0.5") is None


def test_lookup_serial_uses_wmi_when_configured(app, monkeypatch):
    app.config["DISCOVERY_USERNAME"] = "svc"
    app.config["DISCOVERY_PASSWORD"] = "secret"
    app.config["DISCOVERY_DOMAIN"] = "CORP"
    monkeypatch.setattr(
        "app.services.discovery_service._wmi_bios_serial",
        lambda ip, creds: "  delltag9  ",
    )
    assert lookup_serial("10.0.0.5") == "DELLTAG9"
