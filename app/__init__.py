"""Фабрика Flask-приложения: расширения, модели, маршруты.

Веб (Gunicorn), тесты и scheduler_worker вызывают create_app;
процесс опроса HTTP не обслуживает.
"""

import os

from flask import Flask, render_template

from app.config import CONFIGS
from app.extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    from dotenv import load_dotenv

    load_dotenv()

    app = Flask(__name__)
    selected = config_name or os.environ.get("APP_CONFIG", "default")
    config_class = CONFIGS.get(selected)
    if config_class is None:
        raise RuntimeError(f"Неизвестная конфигурация: {selected}")
    app.config.from_object(config_class)

    from app.logging_config import configure_logging

    configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Регистрирует таблицы в metadata до миграций / create_all.
    from app import models

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Войдите, чтобы продолжить."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            numeric_id = int(user_id)
        except (TypeError, ValueError):
            return None
        return db.session.get(models.User, numeric_id)

    _register_blueprints(app)
    _register_error_handlers(app)

    # flask poll — в сервисе планировщика. APScheduler стартует только
    # в scheduler_worker, иначе каждый воркер Gunicorn запустит свой опрос.
    from app.services.scheduler_service import register_commands

    register_commands(app)

    @app.get("/health")
    def health():
        # Без авторизации — для systemd / Nginx probe.
        return {"status": "ok"}

    @app.template_filter("dt")
    def format_dt(value):
        if not value:
            return "—"
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")

    @app.context_processor
    def inject_globals():
        return {"app_name": app.config["APP_NAME"]}

    @app.shell_context_processor
    def shell_context():
        return {"db": db, **{name: getattr(models, name) for name in models.__all__}}

    return app


def _register_blueprints(app: Flask) -> None:
    # Импорты здесь, чтобы расширения инициализировались раньше маршрутов
    # и не было цикла route → service → create_app.
    from app.routes.accounts import bp as accounts_bp
    from app.routes.actions import bp as actions_bp
    from app.routes.admin import bp as admin_bp
    from app.routes.password_expiry import bp as password_expiry_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.devices import bp as devices_bp
    from app.routes.diagnostics import bp as diagnostics_bp
    from app.routes.login_services import bp as login_services_bp
    from app.routes.scripts import bp as scripts_bp
    from app.routes.search import bp as search_bp
    from app.routes.sectors import bp as sectors_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(sectors_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(diagnostics_bp)
    app.register_blueprint(scripts_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(actions_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(password_expiry_bp)
    app.register_blueprint(login_services_bp)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("errors/500.html"), 500
