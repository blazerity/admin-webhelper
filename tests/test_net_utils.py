import pytest

from app.services.net_utils import (
    NetworkInputError,
    assert_public_ipv4,
    expand_ranges,
    normalize_mac,
    parse_range,
)


def test_parse_single_ip():
    assert str(parse_range("10.0.0.5")) == "10.0.0.5"


def test_parse_network_host_bits(app):
    assert str(parse_range("10.0.1.15/24")) == "10.0.1.0/24"


def test_reject_huge_network(app):
    with pytest.raises(NetworkInputError):
        parse_range("10.0.0.0/8")


def test_reject_shell_metacharacters():
    with pytest.raises(NetworkInputError):
        assert_public_ipv4("8.8.8.8; rm -rf /")


def test_expand_skips_network_and_broadcast(app):
    hosts = expand_ranges(["10.1.1.0/30"])
    assert hosts == ["10.1.1.1", "10.1.1.2"]


def test_normalize_mac():
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("нет") is None
