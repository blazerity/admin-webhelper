"""Экземпляры расширений Flask без привязки к приложению.

Подключаются в create_app() через init_app. APScheduler здесь
намеренно нет — отдельный процесс scheduler_worker.
"""

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
