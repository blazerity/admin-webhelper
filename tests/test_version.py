"""Файл VERSION — источник номера релиза."""

from pathlib import Path

from app.version import get_version, parse_version, read_version_file


def test_parse_version_accepts_semver():
    assert parse_version("0.1.0\n") == "0.1.0"
    assert parse_version("v0.2.0") == "0.2.0"
    assert parse_version("1.0") == "1.0"
    assert parse_version("not-a-version") == ""
    assert parse_version("") == ""


def test_read_version_file(tmp_path: Path):
    (tmp_path / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    assert read_version_file(tmp_path) == "0.1.0"


def test_package_version_is_readable():
    assert parse_version(get_version())
    assert get_version().startswith("0.")
