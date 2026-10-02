"""Настройки приложения из переменных окружения.

Класс Config читается один раз при создании приложения.
Секреты (пароль БД, LDAP, Fernet, PsExec) не имеют значений
по умолчанию в коде — только в .env / EnvironmentFile systemd.

Для тестов есть TestingConfig: SQLite в памяти, без LDAP и без планировщика.
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
    # pool_pre_ping проверяет соединение перед выдачей из пула.
    # Полезно, если PostgreSQL разорвал простаивающую сессию.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    LOG_FILE = os.environ.get("LOG_FILE", "logs/bawh.log")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    LDAP_HOST = os.environ.get("LDAP_HOST", "")
    LDAP_PORT = int(os.environ.get("LDAP_PORT", "636"))
    LDAP_USE_SSL = _flag("LDAP_USE_SSL", "true")
    LDAP_BASE_DN = os.environ.get("LDAP_BASE_DN", "")
    LDAP_BIND_DN = os.environ.get("LDAP_BIND_DN", "")
    LDAP_BIND_PASSWORD = os.environ.get("LDAP_BIND_PASSWORD", "")
    LDAP_USER_FILTER = os.environ.get("LDAP_USER_FILTER", "(sAMAccountName={username})")
    LDAP_ADMIN_GROUP = os.environ.get("LDAP_ADMIN_GROUP", "bawh-admins")
    LDAP_DOMAIN = os.environ.get("LDAP_DOMAIN", "")

    POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
    MIN_CIDR_PREFIX = int(os.environ.get("MIN_CIDR_PREFIX", "22"))
    MAX_HOSTS_PER_POLL = int(os.environ.get("MAX_HOSTS_PER_POLL", "2048"))

    # TODO: реальные значения задаются в .env или в веб-форме администратора.
    PSEXEC_USERNAME = os.environ.get("PSEXEC_USERNAME", "")
    PSEXEC_DOMAIN = os.environ.get("PSEXEC_DOMAIN", "")
    PSEXEC_PASSWORD = os.environ.get("PSEXEC_PASSWORD", "")
    FERNET_KEY = os.environ.get("FERNET_KEY", "")

    SCRIPT_LIBRARY_DIR = os.environ.get("SCRIPT_LIBRARY_DIR", "script_library")
    # Сколько символов лога хранить в script_runs.log_text
    MAX_LOG_CHARS = 200_000
    MAX_REMOTE_COMMAND_CHARS = 4_000

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", "0")

    # Планировщик в процессе Gunicorn не включаем.
    SCHEDULER_ENABLED = False

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
    SCHEDULER_ENABLED = False
    LDAP_HOST = "ldap.invalid"
    LDAP_BASE_DN = "DC=example,DC=com"
    LDAP_ADMIN_GROUP = "bawh-admins"
    LDAP_DOMAIN = "example.com"
    FERNET_KEY = os.environ.get("FERNET_KEY", "")


class DevelopmentConfig(Config):
    DEBUG = True


CONFIGS = {
    "default": Config,
    "development": DevelopmentConfig,
    "testing": TestingConfig,
}
