"""Подписка пользователя на устройство (offline-алерты)."""

from app.extensions import db
from app.utils import utcnow


class DeviceWatchlist(db.Model):
    __tablename__ = "device_watchlist"
    __table_args__ = (
        db.UniqueConstraint("user_id", "device_id", name="uq_device_watchlist_user_device"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = db.Column(
        db.Integer,
        db.ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offline_minutes = db.Column(db.Integer, nullable=False, default=15)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User")
    device = db.relationship("Device")

    def __repr__(self) -> str:
        return f"<DeviceWatchlist user={self.user_id} device={self.device_id}>"
