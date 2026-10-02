"""Суточная ротация уже в logging_config; здесь — месячная упаковка.

TimedRotatingFileHandler пишет хвосты рядом с активным файлом:
  logs/bawh.log           — текущий день
  logs/bawh.log.2026-09-30 — вчера и раньше

Этот модуль:
1. Собирает суточные файлы завершённых месяцев в logs/archive/bawh-YYYY-MM.tar.gz
2. Удаляет упакованные суточные файлы
3. Чистит месячные архивы старше LOG_ARCHIVE_KEEP_MONTHS

Запускается из планировщика раз в сутки и вручную: `flask archive-logs`.
"""

from __future__ import annotations

import logging
import os
import re
import tarfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Имя активного файла + точка + YYYY-MM-DD (как у TimedRotatingFileHandler).
_DAILY_SUFFIX_RE = re.compile(r"^(?P<stem>.+)\.(?P<day>\d{4}-\d{2}-\d{2})$")
_MONTHLY_NAME_RE = re.compile(r"^bawh-(?P<month>\d{4}-\d{2})\.tar\.gz$")


def archive_rotated_logs(
    log_file: str | os.PathLike[str],
    archive_dir: str | os.PathLike[str] | None = None,
    *,
    keep_months: int = 12,
    today: date | None = None,
) -> dict[str, int]:
    """Упаковать суточные логи прошлых месяцев и подчистить старые архивы.

    Возвращает счётчики: packed_days, created_archives, removed_archives,
    skipped (файлы текущего месяца и сегодня).
    """
    log_path = Path(log_file)
    if archive_dir is None:
        archive_path = log_path.parent / "archive"
    else:
        archive_path = Path(archive_dir)

    stats = {
        "packed_days": 0,
        "created_archives": 0,
        "removed_archives": 0,
        "skipped": 0,
    }
    if not log_path.parent.is_dir():
        return stats

    now = today or date.today()
    current_month = (now.year, now.month)
    stem = log_path.name

    by_month: dict[tuple[int, int], list[Path]] = defaultdict(list)
    for path in sorted(log_path.parent.iterdir()):
        if not path.is_file():
            continue
        parsed = _parse_daily(path, stem)
        if parsed is None:
            continue
        day = parsed
        key = (day.year, day.month)
        if key >= current_month:
            # Текущий месяц оставляем распакованным — проще читать хвост.
            stats["skipped"] += 1
            continue
        by_month[key].append(path)

    if by_month:
        archive_path.mkdir(parents=True, exist_ok=True)

    for (year, month), files in sorted(by_month.items()):
        archive_name = f"bawh-{year:04d}-{month:02d}.tar.gz"
        target = archive_path / archive_name
        # gzip-tar не умеет надёжный append — собираем заново, если архив уже есть.
        previous_members: list[tuple[str, Path]] = []
        staging_dir = None
        try:
            if target.exists():
                staging_dir = archive_path / f".staging-{year:04d}-{month:02d}"
                staging_dir.mkdir(parents=True, exist_ok=True)
                with tarfile.open(target, "r:gz") as existing:
                    # filter="data" — не восстанавливаем симлинки/права снаружи.
                    existing.extractall(staging_dir, filter="data")
                for extracted in staging_dir.iterdir():
                    if extracted.is_file():
                        previous_members.append((extracted.name, extracted))

            with tarfile.open(target, "w:gz") as tar:
                seen: set[str] = set()
                for arcname, path in previous_members:
                    if arcname in seen:
                        continue
                    tar.add(path, arcname=arcname)
                    seen.add(arcname)
                for path in files:
                    arcname = path.name
                    if arcname not in seen:
                        tar.add(path, arcname=arcname)
                        seen.add(arcname)
                    path.unlink(missing_ok=True)
                    stats["packed_days"] += 1
            stats["created_archives"] += 1
            logger.info(
                "Месячный архив логов %s: упаковано %s суточных файлов",
                archive_name,
                len(files),
            )
        except OSError as exc:
            logger.warning("Не удалось упаковать %s: %s", target, exc)
        finally:
            if staging_dir is not None and staging_dir.exists():
                for leftover in staging_dir.iterdir():
                    leftover.unlink(missing_ok=True)
                staging_dir.rmdir()

    if keep_months > 0 and archive_path.is_dir():
        stats["removed_archives"] = _prune_old_archives(
            archive_path, keep_months=keep_months, today=now
        )

    return stats


def _parse_daily(path: Path, stem: str) -> date | None:
    match = _DAILY_SUFFIX_RE.match(path.name)
    if match is None or match.group("stem") != stem:
        return None
    try:
        return datetime.strptime(match.group("day"), "%Y-%m-%d").date()
    except ValueError:
        return None


def _prune_old_archives(archive_dir: Path, *, keep_months: int, today: date) -> int:
    """Удалить bawh-YYYY-MM.tar.gz старше keep_months полных месяцев."""
    cutoff_year = today.year
    cutoff_month = today.month - keep_months
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1
    cutoff = (cutoff_year, cutoff_month)

    removed = 0
    for path in sorted(archive_dir.iterdir()):
        if not path.is_file():
            continue
        match = _MONTHLY_NAME_RE.match(path.name)
        if match is None:
            continue
        try:
            year_s, month_s = match.group("month").split("-")
            key = (int(year_s), int(month_s))
        except ValueError:
            continue
        if key < cutoff:
            try:
                path.unlink()
                removed += 1
                logger.info("Удалён старый архив логов %s", path.name)
            except OSError as exc:
                logger.warning("Не удалось удалить %s: %s", path, exc)
    return removed
