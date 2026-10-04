"""Прогоны ежедневного отчёта об активных устройствах по секторам."""

from __future__ import annotations

from app.extensions import db
from app.utils import utcnow


class SectorDailyReportRun(db.Model):
    """Итог одного прогона + JSON-снимок отчёта для дашборда."""

    __tablename__ = "sector_daily_report_runs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    exit_code = db.Column(db.Integer, nullable=False, default=0)
    send_emails = db.Column(db.Boolean, nullable=False, default=True)
    report_date = db.Column(db.Date, nullable=False, index=True)
    active_total = db.Column(db.Integer, nullable=False, default=0)
    known_total = db.Column(db.Integer, nullable=False, default=0)
    sectors_count = db.Column(db.Integer, nullable=False, default=0)
    sent_count = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=False, default="")
    # scheduled | cli | web | dry-run
    mode = db.Column(db.String(32), nullable=False, default="scheduled")
    report_json = db.Column(db.Text, nullable=False, default="")
