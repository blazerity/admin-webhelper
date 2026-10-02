"""Имя и MAC. Сокет и subprocess подменены, пакетов в сеть нет."""

import socket
from types import SimpleNamespace

import pytest

from app.services.discovery_service import lookup_hostname, lookup_mac
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
