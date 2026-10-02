"""Ключ-значение для настроек и зашифрованная учётка PsExec.

app_settings — простые параметры вроде интервала опроса.
remote_credentials — одна строка на пользователя сайта. Пароли только
в виде шифротекста Fernet. Открытый пароль живёт в памяти на время
вызова pypsexec.
"""

from app.extensions import db
from app.utils import utcnow

# Ключ в app_settings. Читает settings_service.
POLL_INTERVAL_KEY = "poll_interval_seconds"


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RemoteCredential(db.Model):
    """Учётка PsExec одного пользователя сайта.

    user_id уникален: чужие логин и пароль эта строка не отдаёт.
    Пустые username и password_encrypted значат «запуск от входа на сайт».
    Пароль этого входа лежит в login_password_encrypted и обновляется
    при каждом успешном LDAP-входе, форму PsExec он не затирает.
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
    # Результат Fernet.encrypt, строка ASCII. Не пароль.
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
