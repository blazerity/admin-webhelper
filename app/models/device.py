"""Устройства и история опросов.

devices — текущее состояние; device_history — каждая проверка.
При росте истории — партиции PostgreSQL по месяцам (миграция 0001
ещё одной таблицы).
"""

from app.extensions import db
from app.models.base import TimestampMixin
from app.utils import utcnow


class DeviceStatus:
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class Device(TimestampMixin, db.Model):
    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True)
    # IPv4 строкой; IPv6 сознательно отложен.
    ip = db.Column(db.String(45), unique=True, nullable=False, index=True)
    hostname = db.Column(db.String(255), nullable=True, index=True)
    # Нормализованный AA:BB:CC:DD:EE:FF; пусто, если ARP не видел.
    mac = db.Column(db.String(17), nullable=True, index=True)
    sector_id = db.Column(
        db.Integer,
        db.ForeignKey("sectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    last_seen = db.Column(db.DateTime(timezone=True), nullable=True)
    last_status = db.Column(
        db.String(16),
        nullable=False,
        default=DeviceStatus.UNKNOWN,
        index=True,
    )
    last_response_time_ms = db.Column(db.Integer, nullable=True)

    sector = db.relationship("Sector", back_populates="devices")
    history = db.relationship(
        "DeviceHistory",
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="DeviceHistory.timestamp.desc()",
    )

    @property
    def kind(self) -> str:
        """Тип по префиксу hostname: notebook (N…), desktop/СБ (W…), иначе other."""
        name = (self.hostname or "").lower()
        if name.startswith("n"):
            return "notebook"
        if name.startswith("w"):
            return "desktop"
        return "other"

    def __repr__(self) -> str:
        return f"<Device {self.ip} {self.last_status}>"


class DeviceHistory(db.Model):
    __tablename__ = "device_history"
    __table_args__ = (
        db.Index("ix_device_history_device_time", "device_id", "timestamp"),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    status = db.Column(db.String(16), nullable=False)
    response_time_ms = db.Column(db.Integer, nullable=True)

    device = db.relationship("Device", back_populates="history")
