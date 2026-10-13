"""История уведомлений о сроке пароля AD и прогоны проверки.

LDAP host/base — из .env; bind и SMTP — из настроек модуля (app_settings) с fallback на .env.
Пороги, получатели и расписание — в app_settings.
"""

from __future__ import annotations

from app.extensions import db
from app.utils import utcnow


class PasswordNotification(db.Model):
    """Одна запись цикла уведомлений (аналог строки CSV в standalone-сервисе)."""

    __tablename__ = "password_notifications"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(128), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, default="")
    full_name = db.Column(db.String(255), nullable=False, default="")
    notification_date = db.Column(db.Date, nullable=False, index=True)
    days_left_at_send = db.Column(db.Integer, nullable=True)
    # upcoming | overdue | resolved
    status = db.Column(db.String(32), nullable=False)
    pwd_last_set_snapshot = db.Column(db.String(64), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class PasswordExpiryRun(db.Model):
    """Итог одного прогона + JSON-снимок отчёта для дашборда."""

    __tablename__ = "password_expiry_runs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    exit_code = db.Column(db.Integer, nullable=False, default=0)
    send_emails = db.Column(db.Boolean, nullable=False, default=True)
    user_mail_paused = db.Column(db.Boolean, nullable=False, default=False)
    users_count = db.Column(db.Integer, nullable=False, default=0)
    sent_count = db.Column(db.Integer, nullable=False, default=0)
    resolved_count = db.Column(db.Integer, nullable=False, default=0)
    upcoming_count = db.Column(db.Integer, nullable=False, default=0)
    overdue_count = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=False, default="")
    # scheduled | manual | cli | web | dry-run
    mode = db.Column(db.String(32), nullable=False, default="scheduled")
    report_json = db.Column(db.Text, nullable=False, default="")


class VacationNotice(db.Model):
    """Письмо кадров, которое сервис уже разобрал (или не смог)."""

    __tablename__ = "vacation_notices"

    id = db.Column(db.Integer, primary_key=True)
    message_key = db.Column(db.String(512), nullable=False, unique=True, index=True)
    subject = db.Column(db.String(500), nullable=False, default="")
    vacation_start = db.Column(db.Date, nullable=True)
    vacation_days = db.Column(db.Integer, nullable=False, default=14)
    employee_lines = db.Column(db.Text, nullable=False, default="")
    # ok | partial | error
    status = db.Column(db.String(32), nullable=False, default="ok")
    error = db.Column(db.Text, nullable=False, default="")
    employees_count = db.Column(db.Integer, nullable=False, default=0)
    risk_count = db.Column(db.Integer, nullable=False, default=0)
    unmatched_count = db.Column(db.Integer, nullable=False, default=0)
    # scheduled | cli | web | dry-run
    mode = db.Column(db.String(32), nullable=False, default="scheduled")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class VacationPasswordCheck(db.Model):
    """Проверка одного сотрудника из письма: истечёт ли пароль за отпуск."""

    __tablename__ = "vacation_password_checks"

    id = db.Column(db.Integer, primary_key=True)
    notice_id = db.Column(
        db.Integer,
        db.ForeignKey("vacation_notices.id"),
        nullable=False,
        index=True,
    )
    employee_name = db.Column(db.String(255), nullable=False, default="")
    username = db.Column(db.String(128), nullable=False, default="")
    email = db.Column(db.String(255), nullable=False, default="")
    full_name = db.Column(db.String(255), nullable=False, default="")
    # matched | ambiguous | not_found
    match_status = db.Column(db.String(32), nullable=False, default="not_found")
    days_left = db.Column(db.Integer, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    vacation_start = db.Column(db.Date, nullable=False)
    vacation_days = db.Column(db.Integer, nullable=False, default=14)
    expires_during_vacation = db.Column(db.Boolean, nullable=False, default=False)
    user_notified = db.Column(db.Boolean, nullable=False, default=False)
    note = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
