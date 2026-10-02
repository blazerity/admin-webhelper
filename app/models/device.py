"""Устройства и история опросов.

devices — текущее состояние (последний статус, имя, MAC).
device_history — каждая проверка отдельной строкой.

Когда строк станет очень много, device_history стоит перевести
на партиции PostgreSQL по месяцам:

    CREATE TABLE device_history (...) PARTITION BY RANGE (timestamp);

Миграция 0001 этого ещё не делает: одна таблица и индекс
(device_id, timestamp) проще для первого запуска. Граница
будущего разреза — эта таблица, её не нужно смешивать с devices.
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
    # IPv4 в виде строки. IPv6 сознательно отложен: пинг и CIDR проще объяснять на v4.
    ip = db.Column(db.String(45), unique=True, nullable=False, index=True)
    hostname = db.Column(db.String(255), nullable=True, index=True)
    # Нормализованный вид AA:BB:CC:DD:EE:FF. Пусто, если ARP не увидел адрес.
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
