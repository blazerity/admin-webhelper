"""Ежедневный отчёт: активные устройства за прошедший день по секторам.

«Активное» устройство — хотя бы одна запись device_history со status=online
в календарных сутках отчётной даты (локальный TZ сервера).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from flask import render_template
from sqlalchemy import func, select

from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, Sector, SectorDailyReportRun
from app.services.password_expiry_settings import get_smtp_settings
from app.services.password_mailer import PasswordMailer, PasswordMailerError
from app.services.sector_daily_report_settings import get_sector_daily_report_settings
from app.utils import utcnow

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()


class RunInProgressError(RuntimeError):
    """Уже выполняется другой прогон."""


@dataclass
class SectorRow:
    sector_id: int
    sector_name: str
    active_count: int
    known_count: int


@dataclass
class StoredReport:
    report_date: str
    generated_at: str
    timezone: str
    sectors: list[SectorRow] = field(default_factory=list)
    active_total: int = 0
    known_total: int = 0
    source: str = "run"

    @property
    def sectors_count(self) -> int:
        return len(self.sectors)


@dataclass
class RunResult:
    exit_code: int
    report_date: str = ""
    active_total: int = 0
    known_total: int = 0
    sectors_count: int = 0
    sent_count: int = 0
    error: str = ""
    report: StoredReport | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def previous_calendar_day(today: date | None = None) -> date:
    """Прошедший календарный день в локальном TZ сервера."""
    if today is not None:
        return today
    local_tz = _local_tzinfo()
    return datetime.now(local_tz).date() - timedelta(days=1)


def day_window_utc(report_date: date) -> tuple[datetime, datetime]:
    """Полуночный интервал [start, end) отчётной даты → UTC для запросов к БД."""
    local_tz = _local_tzinfo()
    start_local = datetime.combine(report_date, time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def build_sector_daily_report(
    *,
    report_date: date | None = None,
    source: str = "run",
) -> StoredReport:
    """Собрать снимок: по каждому сектору active/known за прошедший день."""
    day = previous_calendar_day(report_date)
    start_utc, end_utc = day_window_utc(day)
    local_tz = _local_tzinfo()
    tz_name = getattr(local_tz, "key", None) or str(local_tz)

    sectors = list(
        db.session.scalars(select(Sector).order_by(Sector.name.asc())).all()
    )

    known_rows = db.session.execute(
        select(Device.sector_id, func.count(Device.id)).group_by(Device.sector_id)
    ).all()
    known_by_sector = {int(sector_id): int(count) for sector_id, count in known_rows}

    active_rows = db.session.execute(
        select(Device.sector_id, func.count(func.distinct(DeviceHistory.device_id)))
        .join(DeviceHistory, DeviceHistory.device_id == Device.id)
        .where(
            DeviceHistory.status == DeviceStatus.ONLINE,
            DeviceHistory.timestamp >= start_utc,
            DeviceHistory.timestamp < end_utc,
        )
        .group_by(Device.sector_id)
    ).all()
    active_by_sector = {int(sector_id): int(count) for sector_id, count in active_rows}

    rows: list[SectorRow] = []
    active_total = 0
    known_total = 0
    for sector in sectors:
        active = active_by_sector.get(sector.id, 0)
        known = known_by_sector.get(sector.id, 0)
        active_total += active
        known_total += known
        rows.append(
            SectorRow(
                sector_id=sector.id,
                sector_name=sector.name,
                active_count=active,
                known_count=known,
            )
        )

    return StoredReport(
        report_date=day.isoformat(),
        generated_at=_iso_now(),
        timezone=tz_name,
        sectors=rows,
        active_total=active_total,
        known_total=known_total,
        source=source,
    )


def _persist_run(
    result: RunResult,
    *,
    send_emails: bool,
    mode: str,
    started_at: datetime,
    report: StoredReport | None,
) -> None:
    report_day = date.fromisoformat(result.report_date) if result.report_date else previous_calendar_day()
    row = SectorDailyReportRun(
        started_at=started_at,
        finished_at=utcnow(),
        exit_code=result.exit_code,
        send_emails=send_emails,
        report_date=report_day,
        active_total=result.active_total,
        known_total=result.known_total,
        sectors_count=result.sectors_count,
        sent_count=result.sent_count,
        error=result.error or "",
        mode=mode,
        report_json=json.dumps(asdict(report), ensure_ascii=False) if report else "",
    )
    db.session.add(row)
    db.session.commit()


def load_last_run() -> SectorDailyReportRun | None:
    return db.session.scalars(
        select(SectorDailyReportRun).order_by(SectorDailyReportRun.id.desc()).limit(1)
    ).first()


def load_last_report() -> StoredReport | None:
    run = load_last_run()
    if run is None or not run.report_json:
        return None
    try:
        raw: dict[str, Any] = json.loads(run.report_json)
    except json.JSONDecodeError:
        return None
    sectors = [
        SectorRow(
            sector_id=int(item.get("sector_id") or 0),
            sector_name=str(item.get("sector_name") or ""),
            active_count=int(item.get("active_count") or 0),
            known_count=int(item.get("known_count") or 0),
        )
        for item in raw.get("sectors") or []
    ]
    return StoredReport(
        report_date=str(raw.get("report_date") or ""),
        generated_at=str(raw.get("generated_at") or ""),
        timezone=str(raw.get("timezone") or ""),
        sectors=sectors,
        active_total=int(raw.get("active_total") or 0),
        known_total=int(raw.get("known_total") or 0),
        source=str(raw.get("source") or "run"),
    )


def run_sector_daily_report(
    *,
    send_emails: bool = True,
    mode: str = "scheduled",
    report_date: date | None = None,
) -> RunResult:
    """Полный прогон. Без параллельных запусков в одном процессе."""
    if not _run_lock.acquire(blocking=False):
        raise RunInProgressError("Прогон уже выполняется")
    started = utcnow()
    try:
        result = _run_unlocked(
            send_emails=send_emails,
            mode=mode,
            report_date=report_date,
        )
        _persist_run(
            result,
            send_emails=send_emails,
            mode=mode,
            started_at=started,
            report=result.report,
        )
        return result
    except Exception:
        db.session.rollback()
        raise
    finally:
        _run_lock.release()


def _run_unlocked(
    *,
    send_emails: bool,
    mode: str,
    report_date: date | None,
) -> RunResult:
    settings = get_sector_daily_report_settings()
    mailer = PasswordMailer(get_smtp_settings(), dry_run=not send_emails)

    if not send_emails:
        logger.info("Отчёт по секторам: dry-run — SMTP не используется")

    try:
        report = build_sector_daily_report(report_date=report_date, source=mode)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Отчёт по секторам: сбой построения")
        return RunResult(exit_code=1, error=str(exc))

    sent = 0
    if settings.recipients:
        try:
            html = render_template(
                "email/sector_daily_report.html",
                report=report,
                report_date=report.report_date,
                sectors=report.sectors,
                active_total=report.active_total,
                known_total=report.known_total,
                timezone=report.timezone,
            )
            mailer.send_html(
                settings.recipients,
                f"Активные устройства по секторам — {report.report_date}",
                html,
            )
            sent = 1 if send_emails else 0
        except PasswordMailerError as exc:
            logger.exception("Отчёт по секторам: не удалось отправить письмо")
            return RunResult(
                exit_code=1,
                report_date=report.report_date,
                active_total=report.active_total,
                known_total=report.known_total,
                sectors_count=report.sectors_count,
                sent_count=0,
                error=str(exc),
                report=report,
            )
    else:
        logger.info("Отчёт по секторам: получатели не заданы — письмо не отправлено")

    logger.info(
        "Отчёт по секторам: готово date=%s active=%s known=%s sectors=%s sent=%s",
        report.report_date,
        report.active_total,
        report.known_total,
        report.sectors_count,
        sent,
    )
    return RunResult(
        exit_code=0,
        report_date=report.report_date,
        active_total=report.active_total,
        known_total=report.known_total,
        sectors_count=report.sectors_count,
        sent_count=sent,
        report=report,
    )
