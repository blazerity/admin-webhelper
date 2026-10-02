"""Сборка секций административного отчёта по сроку паролей."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.services.password_ad_client import AdUser
from app.services.password_notification_tracker import HistoryRecord


@dataclass(frozen=True)
class ReportRow:
    full_name: str
    username: str
    email: str
    days_left: int | None
    status_text: str


@dataclass(frozen=True)
class AdminReport:
    report_date: str
    upcoming: list[ReportRow]
    overdue: list[ReportRow]
    resolved: list[ReportRow]

    def to_template_context(self) -> dict[str, object]:
        return {
            "report_date": self.report_date,
            "upcoming": self.upcoming,
            "overdue": self.overdue,
            "resolved": self.resolved,
        }


def _overdue_status_text(user: AdUser) -> str:
    if user.status == "must_change":
        return "Смена при следующем входе"
    days = 0 if user.days_left is None else abs(user.days_left)
    return f"Просрочен на {days} дн."


def build_admin_report(
    users: list[AdUser],
    resolved_records: list[HistoryRecord],
    *,
    first_warning_days: int,
    today: date | None = None,
) -> AdminReport:
    current = today or date.today()
    upcoming = [
        ReportRow(
            full_name=user.full_name,
            username=user.username,
            email=user.email,
            days_left=user.days_left,
            status_text=f"Осталось {user.days_left} дн.",
        )
        for user in users
        if user.status == "upcoming"
        and user.days_left is not None
        and 0 < user.days_left <= first_warning_days
    ]
    overdue = [
        ReportRow(
            full_name=user.full_name,
            username=user.username,
            email=user.email,
            days_left=user.days_left,
            status_text=_overdue_status_text(user),
        )
        for user in users
        if user.status in {"overdue", "must_change"}
    ]
    resolved = [
        ReportRow(
            full_name=item.full_name,
            username=item.username,
            email=item.email,
            days_left=item.days_left_at_send,
            status_text="Пароль сменён",
        )
        for item in resolved_records
    ]

    upcoming.sort(key=lambda row: (row.days_left is None, row.days_left or 0, row.username.lower()))
    overdue.sort(key=lambda row: row.username.lower())
    resolved.sort(key=lambda row: row.username.lower())

    return AdminReport(
        report_date=current.isoformat(),
        upcoming=upcoming,
        overdue=overdue,
        resolved=resolved,
    )
