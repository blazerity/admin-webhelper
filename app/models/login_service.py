"""Сервисы, чья доступность показывается на экране входа."""

from app.extensions import db
from app.models.base import TimestampMixin


class LoginService(TimestampMixin, db.Model):
    """Имя для UI и адрес (IPv4 или FQDN) для ICMP-проверки."""

    __tablename__ = "login_services"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    address = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<LoginService {self.name}>"
