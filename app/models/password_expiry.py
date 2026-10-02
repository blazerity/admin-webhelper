"""История уведомлений о сроке пароля AD и прогоны проверки.

LDAP-сервер и bind берутся из .env (как у входа в bAWH).
Пороги, SMTP-получатели и расписание — в app_settings.
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
