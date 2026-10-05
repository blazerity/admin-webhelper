"""Опрос конфигурации железа Windows: прогоны и история снимков."""

from __future__ import annotations

from app.extensions import db
from app.utils import utcnow


class HardwarePollRun(db.Model):
    """Итог одного прохода опроса железа (не путать с ICMP network_poll_runs)."""

    __tablename__ = "hardware_poll_runs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        index=True,
    )
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    scanned = db.Column(db.Integer, nullable=False, default=0)
    online = db.Column(db.Integer, nullable=False, default=0)
    offline = db.Column(db.Integer, nullable=False, default=0)
    collected = db.Column(db.Integer, nullable=False, default=0)
    changed = db.Column(db.Integer, nullable=False, default=0)
    skipped = db.Column(db.Integer, nullable=False, default=0)
    errors = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=False, default="")
    # scheduled | manual | cli | dry-run
    mode = db.Column(db.String(32), nullable=False, default="scheduled")


class DeviceHardwareHistory(db.Model):
    """Снимок железа, если конфигурация изменилась относительно прошлого."""

    __tablename__ = "device_hardware_history"
    __table_args__ = (
        db.Index("ix_device_hw_history_device_time", "device_id", "collected_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    collected_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        index=True,
    )
    cpu_name = db.Column(db.String(255), nullable=True)
    ram_gb = db.Column(db.Integer, nullable=True)
    disk_gb = db.Column(db.Integer, nullable=True)
    os_caption = db.Column(db.String(255), nullable=True)
    os_family = db.Column(db.String(64), nullable=True)
    os_edition = db.Column(db.String(64), nullable=True)
    os_display_version = db.Column(db.String(32), nullable=True)
    os_build = db.Column(db.String(32), nullable=True)

    device = db.relationship("Device", back_populates="hardware_history")

    @property
    def os_label(self) -> str:
        from app.services.hardware_info import format_os_label

        return format_os_label(self.os_family, self.os_edition, self.os_display_version)
