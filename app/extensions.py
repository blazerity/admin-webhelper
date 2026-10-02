"""Экземпляры расширений Flask.

Они создаются здесь, без привязки к приложению, а подключаются
в create_app() через init_app. Так тесты могут собрать второе
приложение, не импортируя маршруты по кругу.

Планировщик APScheduler намеренно не живёт здесь: его процесс
отдельный (app/scheduler_worker.py). Иначе каждый воркер Gunicorn
запустил бы свой опрос.
"""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
