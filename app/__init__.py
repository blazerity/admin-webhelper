"""Сборка Flask-приложения.

create_app — единственное место, где расширения, модели и маршруты
соединяются. Веб-процесс (Gunicorn) и тесты вызывают её.
Процесс опроса (scheduler_worker) тоже создаёт приложение,
чтобы была конфигурация и доступ к БД, но не обслуживает HTTP.

Слои, которые подключаются ниже:
- models    таблицы
- routes    HTTP (blueprints). Внутри они вызывают services.
- authz     кто что видит (используется из routes, не отсюда)
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

    # Импорт регистрирует таблицы в metadata до первой миграции и create_all.
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

    # Команда `flask poll` живёт в сервисе планировщика.
    # Сам APScheduler здесь не стартует: его поднимает scheduler_worker,
    # иначе каждый воркер Gunicorn начал бы свой опрос.
    from app.services.scheduler_service import register_commands

    register_commands(app)

    @app.get("/health")
    def health():
        # Без авторизации: так systemd или Nginx могут проверить, что процесс жив.
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
    # Импорты внутри функции, чтобы создать расширения раньше маршрутов
    # и не словить цикл «route -> service -> create_app».
    from app.routes.admin import bp as admin_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.devices import bp as devices_bp
    from app.routes.diagnostics import bp as diagnostics_bp
    from app.routes.scripts import bp as scripts_bp
    from app.routes.search import bp as search_bp
    from app.routes.sectors import bp as sectors_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(sectors_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(diagnostics_bp)
    app.register_blueprint(scripts_bp)
    app.register_blueprint(admin_bp)


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
