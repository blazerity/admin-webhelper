"""Периодический опрос устройств.

Этот модуль нельзя запускать внутри Gunicorn. У веб-сервера несколько
процессов-воркеров, и каждый выполнил бы start_scheduler: сеть пинговали
бы параллельно, а история в БД задвоилась. Опрос живёт в отдельном
процессе ОС — app/scheduler_worker.py.

Позже вместо APScheduler может появиться Celery beat. Задача beat
вызовет ту же start_scheduler или напрямую poll_all_sectors.
Сама проверка хостов от очереди не зависит и не переписывается.

Команда `flask poll` регистрируется здесь, а create_app вызывает
register_commands. Повторный вызов на том же приложении ничего не делает.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.extensions import db
from app.services.ping_service import poll_all_sectors
from app.services.settings_service import get_poll_interval_seconds

logger = logging.getLogger(__name__)

# Ключ в app.extensions: один планировщик на объект приложения.
SCHEDULER_EXT_KEY = "bawh_scheduler"
_POLL_COMMAND_KEY = "bawh_poll_command"
JOB_ID = "poll-devices"


def register_commands(app) -> None:
    """Команда `flask poll`: один проход и печать сводки.

    Повторный вызов ничего не делает. Иначе Click упадёт
    с «command already registered», когда create_app вызовут дважды
    (так устроены тесты).
    """
    if app.extensions.get(_POLL_COMMAND_KEY):
        return
    if "poll" in app.cli.commands:
        app.extensions[_POLL_COMMAND_KEY] = True
        return

    @app.cli.command("poll")
    def poll() -> None:
        """Опросить все секторы один раз и напечатать сводку."""
        # flask уже открывает контекст, но опрос обязан работать и тогда,
        # когда команду вызовут не из CLI. Вложенный контекст это не ломает.
        with app.app_context():
            stats = poll_all_sectors()
            print(stats)

    app.extensions[_POLL_COMMAND_KEY] = True


def start_scheduler(app) -> BackgroundScheduler:
    """Фоновый APScheduler. Вернёт уже запущенный, если он есть.

    Часовой пояс UTC — метки в БД тоже UTC, интервал не прыгает
    на переходе на летнее время.

    Задача poll-devices:
    max_instances=1 — новый проход не стартует, пока предыдущий пингует;
    coalesce=True — если процесс спал и пропустил несколько тиков,
    после пробуждения выполнится один проход, а не пачка подряд.

    Интервал читается из app_settings в момент старта. В конце каждого
    прохода читаем его снова: форма администратора пишет новое число в БД,
    и следующий тик уже с новым интервалом, без перезапуска процесса.

    Запускать только из scheduler_worker, не из воркера Gunicorn.
    Иначе каждый веб-процесс начнёт пинговать сеть. Когда появится
    Celery beat, он вызовет эту же функцию; poll_all_sectors останется прежним.
    """
    existing = app.extensions.get(SCHEDULER_EXT_KEY)
    if existing is not None and getattr(existing, "running", False):
        return existing

    scheduler = BackgroundScheduler(timezone="UTC")
    # Сколько секунд сейчас стоит у задачи. Сравниваем с БД после прохода.
    scheduled_for = {"seconds": _read_interval(app)}

    def poll_job() -> None:
        # У планировщика нет HTTP-запроса, который открыл бы контекст сам.
        with app.app_context():
            try:
                poll_all_sectors()
            finally:
                # Даже если опрос упал, интервал из формы должен примениться,
                # а соединение с БД — вернуться в пул.
                try:
                    db.session.rollback()
                    _apply_interval(scheduler, scheduled_for)
                except Exception:
                    logger.exception("Не удалось обновить интервал опроса")
                finally:
                    db.session.remove()

    scheduler.add_job(
        poll_job,
        "interval",
        seconds=scheduled_for["seconds"],
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    app.extensions[SCHEDULER_EXT_KEY] = scheduler
    logger.info("Планировщик опроса запущен, интервал %s с", scheduled_for["seconds"])
    return scheduler


def _read_interval(app) -> int:
    with app.app_context():
        return get_poll_interval_seconds()


def _apply_interval(scheduler: BackgroundScheduler, scheduled_for: dict[str, int]) -> None:
    """Переставляет задачу, если в app_settings другое число секунд.

    Вызывать при открытом app context: чтение настройки ходит в БД.
    rollback перед этим снимает только незавершённую транзакцию.
    Уже закоммиченные результаты опроса rollback не стирает.
    """
    seconds = get_poll_interval_seconds()
    if seconds == scheduled_for["seconds"]:
        return
    scheduler.reschedule_job(JOB_ID, trigger="interval", seconds=seconds)
    logger.info(
        "Интервал опроса изменён с %s на %s с. Перезапуск процесса не нужен.",
        scheduled_for["seconds"],
        seconds,
    )
    scheduled_for["seconds"] = seconds
