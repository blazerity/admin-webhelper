"""Справочник УЗ на конечных точках и журнал появлений.

users — операторы сайта. endpoint_accounts — AD/локальные учётки на ПК.
device_account_history — факты «УЗ замечена на устройстве».
"""

from app.extensions import db
from app.utils import utcnow


class SessionType:
    INTERACTIVE = "interactive"
    UNKNOWN = "unknown"


class EndpointAccount(db.Model):
    """Учётная запись, замеченная на устройстве (не оператор сайта)."""

    __tablename__ = "endpoint_accounts"
    __table_args__ = (
        db.UniqueConstraint("domain", "username", name="uq_endpoint_accounts_domain_user"),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(128), nullable=False, index=True)  # lower-case
    domain = db.Column(db.String(128), nullable=False, default="", index=True)  # NetBIOS upper
    display_name = db.Column(db.String(255), nullable=False, default="")
    email = db.Column(db.String(255), nullable=True)
    ldap_dn = db.Column(db.String(512), nullable=True)
    first_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    sightings = db.relationship(
        "DeviceAccountHistory",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    current_devices = db.relationship(
        "Device",
        back_populates="current_account",
        foreign_keys="Device.current_account_id",
    )

    @property
    def account_key(self) -> str:
        if self.domain:
            return f"{self.domain}\\{self.username}"
        return self.username

    @property
    def label(self) -> str:
        if self.display_name:
            return f"{self.display_name} ({self.account_key})"
        return self.account_key

    def __repr__(self) -> str:
        return f"<EndpointAccount {self.account_key}>"


class DeviceAccountHistory(db.Model):
    """Факт: УЗ видели на устройстве в момент опроса."""

    __tablename__ = "device_account_history"
    __table_args__ = (
        db.Index("ix_device_account_history_account_time", "account_id", "seen_at"),
        db.Index("ix_device_account_history_device_time", "device_id", "seen_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("endpoint_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    session_type = db.Column(db.String(32), nullable=False, default=SessionType.UNKNOWN)
    raw_value = db.Column(db.String(255), nullable=False, default="")

    device = db.relationship("Device", back_populates="account_history")
    account = db.relationship("EndpointAccount", back_populates="sightings")
