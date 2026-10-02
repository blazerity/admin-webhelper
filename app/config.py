"""Настройки приложения из переменных окружения.

Секреты задаются только в .env / EnvironmentFile. TestingConfig —
SQLite в памяти, без LDAP и планировщика.
"""

import os

from sqlalchemy.pool import StaticPool


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    APP_NAME = os.environ.get("APP_NAME", "bAWH")
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://bawh:bawh@localhost:5432/bawh",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Проверка соединения перед выдачей из пула (PostgreSQL мог оборвать idle).
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    LOG_FILE = os.environ.get("LOG_FILE", "logs/bawh.log")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    # Суточные хвосты (bawh.log.YYYY-MM-DD) текущего месяца лежат рядом с LOG_FILE.
    # Завершённые месяцы пакуются в LOG_ARCHIVE_DIR; старше N месяцев удаляются.
    LOG_ARCHIVE_DIR = os.environ.get("LOG_ARCHIVE_DIR", "logs/archive")
    LOG_ARCHIVE_KEEP_MONTHS = int(os.environ.get("LOG_ARCHIVE_KEEP_MONTHS", "12"))

    LDAP_HOST = os.environ.get("LDAP_HOST", "")
    LDAP_PORT = int(os.environ.get("LDAP_PORT", "636"))
    LDAP_USE_SSL = _flag("LDAP_USE_SSL", "true")
    LDAP_BASE_DN = os.environ.get("LDAP_BASE_DN", "")
    LDAP_BIND_DN = os.environ.get("LDAP_BIND_DN", "")
    LDAP_BIND_PASSWORD = os.environ.get("LDAP_BIND_PASSWORD", "")
    LDAP_USER_FILTER = os.environ.get("LDAP_USER_FILTER", "(sAMAccountName={username})")
    LDAP_ADMIN_GROUP = os.environ.get("LDAP_ADMIN_GROUP", "bawh-admins")
    LDAP_DOMAIN = os.environ.get("LDAP_DOMAIN", "")

    # SMTP для модуля уведомлений о сроке паролей AD (не дублирует LDAP_*).
    SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
    SMTP_USE_STARTTLS = _flag("SMTP_USE_STARTTLS", "0")
    SMTP_FROM = os.environ.get("SMTP_FROM", "").strip()
    SMTP_USER = os.environ.get("SMTP_USER", "").strip()
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

    POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
    MIN_CIDR_PREFIX = int(os.environ.get("MIN_CIDR_PREFIX", "22"))
    MAX_HOSTS_PER_POLL = int(os.environ.get("MAX_HOSTS_PER_POLL", "2048"))

    # Учётка для WMI (серийник / service tag) при опросе. Пусто — только hostname.
    DISCOVERY_USERNAME = os.environ.get("DISCOVERY_USERNAME", "").strip()
    DISCOVERY_PASSWORD = os.environ.get("DISCOVERY_PASSWORD", "")
    DISCOVERY_DOMAIN = os.environ.get("DISCOVERY_DOMAIN", "").strip()

    FERNET_KEY = os.environ.get("FERNET_KEY", "")

    PROJECT_ROOT = os.environ.get(
        "PROJECT_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    # Публичный HTTPS-адрес; пусто — берётся origin, если он тоже https.
    GIT_REMOTE_URL = os.environ.get("GIT_REMOTE_URL", "").strip()
    GIT_BRANCH = os.environ.get("GIT_BRANCH", "main").strip() or "main"
    # Сколько последних копий кода хранить (активный откат не удаляется).
    UPDATE_BACKUP_KEEP = int(os.environ.get("UPDATE_BACKUP_KEEP", "5"))
    # После удачной замены перезапустить bawh-web и bawh-scheduler через sudo.
    UPDATE_RESTART = _flag("UPDATE_RESTART", "1")
    # Linux-пользователь для sudo -u при перезапуске (пусто — root по умолчанию).
    # В интерфейсе: Параметры → Обновление из git. Нужен sudoers, см. deploy/bawh-update.sudoers.
    UPDATE_SUDO_USER = os.environ.get("UPDATE_SUDO_USER", "").strip()

    SCRIPT_LIBRARY_DIR = os.environ.get("SCRIPT_LIBRARY_DIR", "script_library")
    MAX_LOG_CHARS = 200_000
    MAX_REMOTE_COMMAND_CHARS = 4_000

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", "0")

    WTF_CSRF_ENABLED = True


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    WTF_CSRF_ENABLED = False
    LDAP_HOST = "ldap.invalid"
    LDAP_BASE_DN = "DC=example,DC=com"
    LDAP_ADMIN_GROUP = "bawh-admins"
    LDAP_DOMAIN = "example.com"
    FERNET_KEY = os.environ.get("FERNET_KEY", "")
    UPDATE_RESTART = False


class DevelopmentConfig(Config):
    DEBUG = True


CONFIGS = {
    "default": Config,
    "development": DevelopmentConfig,
    "testing": TestingConfig,
}
