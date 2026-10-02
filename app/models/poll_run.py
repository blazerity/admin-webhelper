"""Журнал прогонов опроса сети.

Пишется обёрткой run_network_poll (планировщик, CLI, ручной запуск из Параметров).
Сама проверка хостов — в ping_service.poll_all_sectors.
"""

from __future__ import annotations

from app.extensions import db
from app.utils import utcnow


class NetworkPollRun(db.Model):
    """Итог одного прохода опроса всех секторов."""

    __tablename__ = "network_poll_runs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    scanned = db.Column(db.Integer, nullable=False, default=0)
    online = db.Column(db.Integer, nullable=False, default=0)
    offline = db.Column(db.Integer, nullable=False, default=0)
    errors = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=False, default="")
    # scheduled | manual | cli
    mode = db.Column(db.String(32), nullable=False, default="scheduled")
