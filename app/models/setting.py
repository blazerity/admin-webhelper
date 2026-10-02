"""Ключ-значение настроек и зашифрованная учётка PsExec.

Пароли только как шифротекст Fernet; открытый — в памяти на время pypsexec.
"""

from app.extensions import db
from app.utils import utcnow

POLL_INTERVAL_KEY = "poll_interval_seconds"


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RemoteCredential(db.Model):
    """Учётка PsExec одного пользователя сайта (одна строка на user_id).

    Пустые username/password_encrypted — запуск от входа на сайт
    (login_password_encrypted обновляется при LDAP-логине).
    """

    __tablename__ = "remote_credentials"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    username = db.Column(db.String(128), nullable=False, default="")
    domain = db.Column(db.String(128), nullable=False, default="")
    # Fernet.encrypt (ASCII), не пароль.
    password_encrypted = db.Column(db.Text, nullable=False, default="")
    login_password_encrypted = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    updated_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user = db.relationship("User", foreign_keys=[user_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
