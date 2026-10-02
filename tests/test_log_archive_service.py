"""Архивация суточных логов в месячные tar.gz."""

from datetime import date
from pathlib import Path

import tarfile

from app.services.log_archive_service import archive_rotated_logs


def test_archive_packs_previous_month_and_keeps_current(tmp_path: Path):
    log_file = tmp_path / "bawh.log"
    log_file.write_text("current\n", encoding="utf-8")
    archive_dir = tmp_path / "archive"

    (tmp_path / "bawh.log.2026-08-01").write_text("aug1\n", encoding="utf-8")
    (tmp_path / "bawh.log.2026-08-15").write_text("aug15\n", encoding="utf-8")
    (tmp_path / "bawh.log.2026-09-30").write_text("sep\n", encoding="utf-8")
    (tmp_path / "bawh.log.2026-10-01").write_text("oct\n", encoding="utf-8")

    stats = archive_rotated_logs(
        log_file,
        archive_dir,
        keep_months=12,
        today=date(2026, 10, 2),
    )

    assert stats["packed_days"] == 3
    assert stats["skipped"] == 1
    assert stats["created_archives"] == 2
    assert not (tmp_path / "bawh.log.2026-08-01").exists()
    assert not (tmp_path / "bawh.log.2026-08-15").exists()
    assert not (tmp_path / "bawh.log.2026-09-30").exists()
    assert (tmp_path / "bawh.log.2026-10-01").exists()
    assert log_file.exists()

    august = archive_dir / "bawh-2026-08.tar.gz"
    september = archive_dir / "bawh-2026-09.tar.gz"
    assert august.is_file()
    assert september.is_file()
    with tarfile.open(august, "r:gz") as tar:
        names = set(tar.getnames())
    assert names == {"bawh.log.2026-08-01", "bawh.log.2026-08-15"}


def test_archive_prunes_old_monthly_files(tmp_path: Path):
    log_file = tmp_path / "bawh.log"
    log_file.write_text("current\n", encoding="utf-8")
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    old = archive_dir / "bawh-2025-01.tar.gz"
    recent = archive_dir / "bawh-2026-08.tar.gz"
    for path in (old, recent):
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo(name="dummy")
            info.size = 0
            tar.addfile(info)

    stats = archive_rotated_logs(
        log_file,
        archive_dir,
        keep_months=3,
        today=date(2026, 10, 2),
    )
    assert stats["removed_archives"] == 1
    assert not old.exists()
    assert recent.exists()
