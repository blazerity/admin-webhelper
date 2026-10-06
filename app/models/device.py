"""Устройства и история опросов.

devices — текущее состояние; device_history — каждая проверка.
Уникальность машины: только serial_number (WMI service tag).
Hostname — отображение, не ключ слияния.
IP на карточке — «основной» (последний успешный онлайн-зонд).
device_addresses — все виденные IP/MAC одной машины (Wi‑Fi + Ethernet и т.п.).
Без SN можно держать заглушку на текущем IP (камера / Linux / WMI не ответил).
current_account_id — УЗ, которую последний опрос видел на машине.
"""

from app.extensions import db
from app.models.base import TimestampMixin
from app.services.device_kind import (
    classify_device_kind,
    kind_label,
    kind_shows_accounts,
    kind_shows_commands,
    kind_shows_hardware,
    kind_shows_mac,
    kind_shows_serial,
)
from app.utils import utcnow


class DeviceStatus:
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class Device(TimestampMixin, db.Model):
    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True)
    # IPv4 строкой; IPv6 сознательно отложен. Не unique: машина может сменить адрес.
    ip = db.Column(db.String(45), nullable=False, index=True)
    hostname = db.Column(db.String(255), nullable=True, index=True)
    # Имя с машины (WMI), не PTR. PTR — запасной, пока WMI не ответил.
    # Service tag / серийник (Win32_BIOS). Единственный ключ идентичности.
    serial_number = db.Column(db.String(64), nullable=True, unique=True, index=True)
    # Нормализованный AA:BB:CC:DD:EE:FF; пусто, если ARP не видел.
    mac = db.Column(db.String(17), nullable=True, index=True)
    sector_id = db.Column(
        db.Integer,
        db.ForeignKey("sectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    current_account_id = db.Column(
        db.Integer,
        db.ForeignKey("endpoint_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    current_account_seen_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_seen = db.Column(db.DateTime(timezone=True), nullable=True)
    last_status = db.Column(
        db.String(16),
        nullable=False,
        default=DeviceStatus.UNKNOWN,
        index=True,
    )
    last_response_time_ms = db.Column(db.Integer, nullable=True)
    # Отпечаток серого адреса: windows/router/camera/printer. Пишет poll, не пользователь.
    fingerprint_kind = db.Column(db.String(16), nullable=True)
    fingerprint_detail = db.Column(db.String(255), nullable=True)
    # Снимок железа (отдельный WMI-опрос, не ICMP). Пусто — ещё не собирали.
    cpu_name = db.Column(db.String(255), nullable=True)
    ram_gb = db.Column(db.Integer, nullable=True)
    disk_gb = db.Column(db.Integer, nullable=True)
    os_caption = db.Column(db.String(255), nullable=True)
    os_family = db.Column(db.String(64), nullable=True)
    os_edition = db.Column(db.String(64), nullable=True)
    os_display_version = db.Column(db.String(32), nullable=True)
    os_build = db.Column(db.String(32), nullable=True)
    hardware_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    sector = db.relationship("Sector", back_populates="devices")
    current_account = db.relationship(
        "EndpointAccount",
        back_populates="current_devices",
        foreign_keys=[current_account_id],
    )
    history = db.relationship(
        "DeviceHistory",
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="DeviceHistory.timestamp.desc()",
    )
    account_history = db.relationship(
        "DeviceAccountHistory",
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="DeviceAccountHistory.seen_at.desc()",
    )
    hardware_history = db.relationship(
        "DeviceHardwareHistory",
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="DeviceHardwareHistory.collected_at.desc()",
    )
    addresses = db.relationship(
        "DeviceAddress",
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="DeviceAddress.last_seen.desc()",
    )

    @property
    def kind(self) -> str:
        """Тип по hostname, WMI-серийнику, TCP/SNMP-отпечатку и MAC."""
        return classify_device_kind(
            self.hostname,
            serial_number=self.serial_number,
            mac=self.mac,
            fingerprint_kind=self.fingerprint_kind,
        )

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    @property
    def shows_accounts(self) -> bool:
        """УЗ на карточке — ноутбуки, СБ и «Прочее»."""
        return kind_shows_accounts(self.kind)

    @property
    def shows_mac(self) -> bool:
        """MAC — ноут/СБ/прочее и периферия (камера, МФУ, MikroTik)."""
        return kind_shows_mac(self.kind)

    @property
    def shows_serial(self) -> bool:
        """Серийный номер — только ноутбуки и СБ."""
        return kind_shows_serial(self.kind)

    @property
    def shows_hardware(self) -> bool:
        """Вкладка/блок оборудования — ноутбуки, СБ, серверы и «Прочее»."""
        return kind_shows_hardware(self.kind)

    @property
    def shows_commands(self) -> bool:
        """Команды и скрипты — ноутбуки, СБ, серверы и «Прочее»."""
        return kind_shows_commands(self.kind)

    @property
    def os_label(self) -> str:
        """Windows 11 Pro 25H2 — пусто, если опрос железа ещё не писал ОС."""
        from app.services.hardware_info import format_os_label

        return format_os_label(self.os_family, self.os_edition, self.os_display_version)

    @property
    def hardware_identity(self) -> tuple:
        return (
            self.cpu_name,
            self.ram_gb,
            self.disk_gb,
            self.os_family,
            self.os_edition,
            self.os_display_version,
            self.os_build,
        )

    def __repr__(self) -> str:
        return f"<Device {self.ip} {self.last_status}>"


class DeviceAddress(db.Model):
    """Сетевой адрес устройства (один из интерфейсов: Ethernet, Wi‑Fi, VPN).

    Карточка ``devices`` хранит основной IP/MAC; здесь — все адреса, с которых
    машина отвечала. Один serial → одна карточка, несколько строк адресов.
    """

    __tablename__ = "device_addresses"
    __table_args__ = (
        db.UniqueConstraint("device_id", "ip", name="uq_device_addresses_device_ip"),
        db.Index("ix_device_addresses_ip", "ip"),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ip = db.Column(db.String(45), nullable=False)
    mac = db.Column(db.String(17), nullable=True)
    last_seen = db.Column(db.DateTime(timezone=True), nullable=True)
    last_status = db.Column(
        db.String(16),
        nullable=False,
        default=DeviceStatus.UNKNOWN,
    )

    device = db.relationship("Device", back_populates="addresses")

    def __repr__(self) -> str:
        return f"<DeviceAddress {self.ip} device={self.device_id}>"


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
