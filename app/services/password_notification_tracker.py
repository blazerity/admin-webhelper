"""История уведомлений в PostgreSQL и решение «слать / пропустить / закрыть цикл»."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import select

from app.extensions import db
from app.models import PasswordNotification
from app.services.password_ad_client import AdUser

logger = logging.getLogger(__name__)

NotificationStatus = Literal["upcoming", "overdue", "resolved"]
NotifyAction = Literal["send", "skip", "resolve"]


@dataclass(frozen=True)
class HistoryRecord:
    username: str
    email: str
    full_name: str
    notification_date: date
    days_left_at_send: int | None
    status: NotificationStatus
    pwd_last_set_snapshot: str


@dataclass(frozen=True)
class NotifyDecision:
    action: NotifyAction
    status: NotificationStatus | None
    reason: str


def _row_to_record(row: PasswordNotification) -> HistoryRecord:
    return HistoryRecord(
        username=row.username,
        email=row.email or "",
        full_name=row.full_name or "",
        notification_date=row.notification_date,
        days_left_at_send=row.days_left_at_send,
        status=row.status,  # type: ignore[arg-type]
        pwd_last_set_snapshot=row.pwd_last_set_snapshot or "",
    )


class NotificationTracker:
    """Читает историю из БД; append копит строки до commit пайплайна."""

    def __init__(self) -> None:
        self._pending: list[PasswordNotification] = []

    def records_for(self, username: str) -> list[HistoryRecord]:
        rows = db.session.scalars(
            select(PasswordNotification)
            .where(PasswordNotification.username == username)
            .order_by(PasswordNotification.id)
        ).all()
        # Также учитываем ещё не закоммиченные append этого прогона.
        pending = [
            _row_to_record(row)
            for row in self._pending
            if row.username.lower() == username.lower()
        ]
        existing = [_row_to_record(row) for row in rows]
        return existing + pending

    def last_record(self, username: str) -> HistoryRecord | None:
        items = self.records_for(username)
        return items[-1] if items else None

    def already_sent_today(self, username: str, today: date) -> bool:
        return any(
            item.notification_date == today and item.status in {"upcoming", "overdue"}
            for item in self.records_for(username)
        )

    def decide(
        self,
        user: AdUser,
        *,
        first_warning_days: int,
        daily_warning_threshold: int,
        today: date | None = None,
    ) -> NotifyDecision:
        current = today or date.today()
        last = self.last_record(user.username)

        if user.status == "must_change":
            return NotifyDecision("skip", None, "требуется смена при входе, письмо не отправляем")

        if last and last.status != "resolved" and last.pwd_last_set_snapshot != user.pwd_last_set_snapshot:
            if user.status == "ok":
                return NotifyDecision("resolve", "resolved", "пароль сменён, цикл уведомлений закрыт")
            return NotifyDecision("resolve", "resolved", "пароль сменён, фиксируем resolved")

        if user.status == "ok":
            return NotifyDecision("skip", None, "срок пароля вне окна уведомлений")

        if self.already_sent_today(user.username, current):
            return NotifyDecision("skip", None, "письмо уже отправлено сегодня")

        cycle_records = [
            item
            for item in self.records_for(user.username)
            if item.pwd_last_set_snapshot == user.pwd_last_set_snapshot and item.status != "resolved"
        ]
        sent_in_cycle = any(item.status in {"upcoming", "overdue"} for item in cycle_records)

        if user.status == "overdue" or (
            user.days_left is not None and user.days_left <= daily_warning_threshold
        ):
            send_status: NotificationStatus = "overdue" if user.status == "overdue" else "upcoming"
            return NotifyDecision("send", send_status, "ежедневное предупреждение")

        if user.status == "upcoming" and user.days_left is not None:
            if user.days_left <= first_warning_days and not sent_in_cycle:
                return NotifyDecision("send", "upcoming", "первое предупреждение")
            return NotifyDecision(
                "skip",
                None,
                "первое письмо уже отправлено, ежедневный порог не достигнут",
            )

        return NotifyDecision("skip", None, "нет правила отправки")

    def append(
        self,
        user: AdUser,
        *,
        status: NotificationStatus,
        today: date | None = None,
    ) -> HistoryRecord:
        current = today or date.today()
        row = PasswordNotification(
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            notification_date=current,
            days_left_at_send=user.days_left,
            status=status,
            pwd_last_set_snapshot=user.pwd_last_set_snapshot,
        )
        self._pending.append(row)
        db.session.add(row)
        return _row_to_record(row)

    def resolved_today(self, today: date | None = None) -> list[HistoryRecord]:
        current = today or date.today()
        return [
            item
            for item in self._all_loaded(current)
            if item.status == "resolved" and item.notification_date == current
        ]

    def _all_loaded(self, today: date) -> list[HistoryRecord]:
        rows = db.session.scalars(
            select(PasswordNotification).where(PasswordNotification.notification_date == today)
        ).all()
        return [_row_to_record(row) for row in rows] + [
            _row_to_record(row)
            for row in self._pending
            if row.notification_date == today
        ]

    def commit(self) -> None:
        db.session.commit()
        self._pending.clear()
