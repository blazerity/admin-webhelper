"""Ключ-значение для настроек и зашифрованная учётка PsExec.

app_settings — простые параметры вроде интервала опроса.
remote_credentials — пароль только в виде шифротекста Fernet.
Открытый пароль живёт в памяти на время вызова pypsexec и в .env
как запасной источник, пока администратор не сохранил его через форму.
"""

from app.extensions import db
from app.utils import utcnow

# Ключ в app_settings. Читает settings_service.
POLL_INTERVAL_KEY = "poll_interval_seconds"
# Имя единственной записи учётки. Позже можно завести несколько записей.
DEFAULT_CREDENTIAL_NAME = "default"


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RemoteCredential(db.Model):
    __tablename__ = "remote_credentials"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False, default=DEFAULT_CREDENTIAL_NAME)
    username = db.Column(db.String(128), nullable=False, default="")
    domain = db.Column(db.String(128), nullable=False, default="")
    # Результат Fernet.encrypt, строка ASCII. Не пароль.
    password_encrypted = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    updated_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    updated_by = db.relationship("User")
