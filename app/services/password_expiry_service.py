"""Пайплайн проверки срока паролей AD и рассылки.

LDAP host/base — из .env; bind и SMTP — из настроек модуля (с fallback на .env);
пороги и расписание — app_settings.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from app.extensions import db
from app.models import PasswordExpiryRun
from app.services.password_ad_client import (
    AdUser,
    PasswordAdError,
    fetch_password_users,
)
from app.services.password_expiry_settings import (
    get_password_expiry_settings,
    get_smtp_settings,
    is_user_mail_paused,
)
from app.services.password_mailer import PasswordMailer, PasswordMailerError
from app.services.password_notification_tracker import NotificationTracker
from app.services.password_report_builder import AdminReport, ReportRow, build_admin_report
from app.utils import utcnow

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()


class RunInProgressError(RuntimeError):
    """Уже выполняется другой прогон."""


@dataclass
class ReportUserSnapshot:
    username: str
    email: str
    full_name: str
    days_left: int | None
    status: str
    status_text: str
    expiry_date: str | None = None
    distinguished_name: str = ""


@dataclass
class StoredReport:
    report_date: str
    generated_at: str
    upcoming: list[ReportUserSnapshot] = field(default_factory=list)
    overdue: list[ReportUserSnapshot] = field(default_factory=list)
    resolved: list[ReportUserSnapshot] = field(default_factory=list)
    all_users: list[ReportUserSnapshot] = field(default_factory=list)
    source: str = "run"

    @property
    def counts(self) -> dict[str, int]:
        return {
            "upcoming": len(self.upcoming),
            "overdue": len(self.overdue),
            "resolved": len(self.resolved),
            "total": len(self.all_users),
        }


@dataclass
class RunResult:
    exit_code: int
    users_count: int = 0
    sent_count: int = 0
    resolved_count: int = 0
    upcoming_count: int = 0
    overdue_count: int = 0
    user_mail_paused: bool = False
    error: str = ""
    report: StoredReport | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _row_to_snapshot(row: ReportRow, status: str) -> ReportUserSnapshot:
    return ReportUserSnapshot(
        username=row.username,
        email=row.email,
        full_name=row.full_name,
        days_left=row.days_left,
        status=status,
        status_text=row.status_text,
    )


def _user_to_snapshot(user: AdUser) -> ReportUserSnapshot:
    if user.status == "must_change":
        status_text = "Смена при следующем входе"
    elif user.status == "overdue":
        days = 0 if user.days_left is None else abs(user.days_left)
        status_text = f"Просрочен на {days} дн."
    elif user.status == "upcoming" and user.days_left is not None:
        status_text = f"Осталось {user.days_left} дн."
    else:
        status_text = "В норме"
    return ReportUserSnapshot(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        days_left=user.days_left,
        status=user.status,
        status_text=status_text,
        expiry_date=user.expiry_date.isoformat() if user.expiry_date else None,
        distinguished_name=user.distinguished_name,
    )


def build_stored_report(
    report: AdminReport,
    users: list[AdUser],
    *,
    source: str = "run",
) -> StoredReport:
    return StoredReport(
        report_date=report.report_date,
        generated_at=_iso_now(),
        upcoming=[_row_to_snapshot(row, "upcoming") for row in report.upcoming],
        overdue=[_row_to_snapshot(row, "overdue") for row in report.overdue],
        resolved=[_row_to_snapshot(row, "resolved") for row in report.resolved],
        all_users=[_user_to_snapshot(user) for user in users],
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
    row = PasswordExpiryRun(
        started_at=started_at,
        finished_at=utcnow(),
        exit_code=result.exit_code,
        send_emails=send_emails,
        user_mail_paused=result.user_mail_paused,
        users_count=result.users_count,
        sent_count=result.sent_count,
        resolved_count=result.resolved_count,
        upcoming_count=result.upcoming_count,
        overdue_count=result.overdue_count,
        error=result.error or "",
        mode=mode,
        report_json=json.dumps(asdict(report), ensure_ascii=False) if report else "",
    )
    db.session.add(row)
    db.session.commit()


def load_last_run() -> PasswordExpiryRun | None:
    return db.session.scalars(
        select(PasswordExpiryRun).order_by(PasswordExpiryRun.id.desc()).limit(1)
    ).first()


def load_last_report() -> StoredReport | None:
    run = load_last_run()
    if run is None or not run.report_json:
        return None
    try:
        raw: dict[str, Any] = json.loads(run.report_json)
    except json.JSONDecodeError:
        return None
    return StoredReport(
        report_date=str(raw.get("report_date") or date.today().isoformat()),
        generated_at=str(raw.get("generated_at") or ""),
        upcoming=[ReportUserSnapshot(**item) for item in raw.get("upcoming") or []],
        overdue=[ReportUserSnapshot(**item) for item in raw.get("overdue") or []],
        resolved=[ReportUserSnapshot(**item) for item in raw.get("resolved") or []],
        all_users=[ReportUserSnapshot(**item) for item in raw.get("all_users") or []],
        source=str(raw.get("source") or "run"),
    )


def run_password_expiry(
    *,
    send_emails: bool = True,
    mode: str = "scheduled",
    today: date | None = None,
) -> RunResult:
    """Полный прогон. Без параллельных запусков в одном процессе."""
    if not _run_lock.acquire(blocking=False):
        raise RunInProgressError("Прогон уже выполняется")
    started = utcnow()
    current = today or date.today()
    try:
        paused = is_user_mail_paused(current)
        result = _run_unlocked(
            current=current,
            send_emails=send_emails,
            mode=mode,
            paused=paused,
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
    current: date,
    send_emails: bool,
    mode: str,
    paused: bool,
) -> RunResult:
    settings = get_password_expiry_settings()
    mailer = PasswordMailer(get_smtp_settings(), dry_run=not send_emails)
    tracker = NotificationTracker()

    if not send_emails:
        logger.info("Пароли AD: dry-run — SMTP и история не пишутся")
    if paused and send_emails:
        logger.info(
            "Пароли AD: пауза пользовательских писем до %s",
            settings.pause_user_mail_until,
        )

    try:
        users = fetch_password_users(settings, today=current)
    except PasswordAdError as exc:
        logger.exception("Пароли AD: ошибка каталога")
        _try_alert(mailer, settings.admin_recipients, str(exc), send_emails=send_emails)
        return RunResult(exit_code=1, user_mail_paused=paused, error=str(exc))

    sent = 0
    resolved = 0
    for user in users:
        decision = tracker.decide(
            user,
            first_warning_days=settings.first_warning_days,
            daily_warning_threshold=settings.daily_warning_threshold,
            today=current,
        )
        if decision.action == "resolve" and decision.status == "resolved":
            if send_emails:
                tracker.append(user, status="resolved", today=current)
            resolved += 1
            decision = tracker.decide(
                user,
                first_warning_days=settings.first_warning_days,
                daily_warning_threshold=settings.daily_warning_threshold,
                today=current,
            )

        if decision.action != "send" or decision.status is None:
            continue

        if paused and send_emails:
            continue

        try:
            _send_user_mail(mailer, settings.instructions_url, user)
        except PasswordMailerError:
            logger.exception("Пароли AD: не удалось отправить письмо %s", user.username)
            continue

        if send_emails:
            tracker.append(user, status=decision.status, today=current)
        sent += 1

    if send_emails:
        tracker.commit()
    else:
        db.session.rollback()

    report = build_admin_report(
        users,
        tracker.resolved_today(current) if send_emails else [],
        first_warning_days=settings.first_warning_days,
        today=current,
    )
    stored = build_stored_report(report, users, source=mode)

    if settings.admin_recipients:
        try:
            html = mailer.render_admin_report(report.to_template_context())
            mailer.send_html(
                settings.admin_recipients,
                f"Отчёт по истекающим паролям — {report.report_date}",
                html,
            )
        except PasswordMailerError:
            logger.exception("Пароли AD: не удалось отправить отчёт администраторам")
            _try_alert(
                mailer,
                settings.admin_recipients,
                "Не удалось отправить сводный отчёт администраторам.",
                send_emails=send_emails,
            )
            return RunResult(
                exit_code=1,
                users_count=len(users),
                sent_count=sent,
                resolved_count=resolved,
                upcoming_count=len(report.upcoming),
                overdue_count=len(report.overdue),
                user_mail_paused=paused,
                error="Не удалось отправить сводный отчёт администраторам.",
                report=stored,
            )
    else:
        logger.info("Пароли AD: получатели админ-отчёта не заданы — письмо не отправлено")

    logger.info(
        "Пароли AD: готово users=%s sent=%s resolved=%s upcoming=%s overdue=%s",
        len(users),
        sent,
        resolved,
        len(report.upcoming),
        len(report.overdue),
    )
    return RunResult(
        exit_code=0,
        users_count=len(users),
        sent_count=sent,
        resolved_count=resolved,
        upcoming_count=len(report.upcoming),
        overdue_count=len(report.overdue),
        user_mail_paused=paused,
        report=stored,
    )


def notify_users_now(
    usernames: list[str],
    *,
    send_emails: bool = True,
    today: date | None = None,
) -> RunResult:
    """Принудительное напоминание выбранным пользователям."""
    current = today or date.today()
    wanted = {name.lower() for name in usernames if name.strip()}
    if not wanted:
        return RunResult(exit_code=1, error="Не выбраны пользователи")

    if not _run_lock.acquire(blocking=False):
        return RunResult(exit_code=2, error="Прогон уже выполняется")

    started = utcnow()
    try:
        settings = get_password_expiry_settings()
        mailer = PasswordMailer(get_smtp_settings(), dry_run=not send_emails)
        tracker = NotificationTracker()
        try:
            users = fetch_password_users(settings, today=current)
        except PasswordAdError as exc:
            result = RunResult(exit_code=1, error=str(exc))
            _persist_run(result, send_emails=send_emails, mode="manual", started_at=started, report=None)
            return result

        matched = [user for user in users if user.username.lower() in wanted]
        if not matched:
            result = RunResult(exit_code=1, error="Выбранные пользователи не найдены в AD")
            _persist_run(result, send_emails=send_emails, mode="manual", started_at=started, report=None)
            return result

        sent = 0
        for user in matched:
            if user.status == "must_change":
                continue
            if tracker.already_sent_today(user.username, current) and send_emails:
                continue
            status = "overdue" if user.status == "overdue" else "upcoming"
            try:
                _send_user_mail(mailer, settings.instructions_url, user)
            except PasswordMailerError as exc:
                result = RunResult(exit_code=1, sent_count=sent, error=str(exc))
                _persist_run(result, send_emails=send_emails, mode="manual", started_at=started, report=None)
                return result
            if send_emails:
                tracker.append(user, status=status, today=current)
            sent += 1

        if send_emails:
            tracker.commit()

        report = build_admin_report(
            users,
            tracker.resolved_today(current),
            first_warning_days=settings.first_warning_days,
            today=current,
        )
        stored = build_stored_report(report, users, source="manual")
        result = RunResult(
            exit_code=0,
            users_count=len(matched),
            sent_count=sent,
            upcoming_count=len(report.upcoming),
            overdue_count=len(report.overdue),
            report=stored,
        )
        _persist_run(result, send_emails=send_emails, mode="manual", started_at=started, report=stored)
        return result
    except Exception:
        db.session.rollback()
        raise
    finally:
        _run_lock.release()


def _send_user_mail(mailer: PasswordMailer, instructions_url: str, user: AdUser) -> None:
    expired = user.status == "overdue"
    html = mailer.render_user_notification(
        full_name=user.full_name,
        username=user.username,
        days_left=user.days_left,
        expired=expired,
        instructions_url=instructions_url,
    )
    mailer.send_html([user.email], "Требуется смена пароля", html)


def _try_alert(
    mailer: PasswordMailer,
    recipients: list[str],
    message: str,
    *,
    send_emails: bool,
) -> None:
    if not send_emails or not recipients:
        logger.critical("Пароли AD: алерт не отправлен: %s", message)
        return
    try:
        mailer.send_alert(recipients, message)
    except PasswordMailerError:
        logger.critical("Пароли AD: SMTP недоступен, алерт не отправлен: %s", message)
