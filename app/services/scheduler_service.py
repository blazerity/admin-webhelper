"""Периодический опрос устройств и проверка срока паролей AD.

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
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.extensions import db
from app.services.log_archive_service import archive_rotated_logs
from app.services.password_expiry_service import run_password_expiry
from app.services.password_expiry_settings import (
    DEFAULT_SCHEDULE_CRON,
    get_password_expiry_settings,
)
from app.services.ping_service import PollInProgressError, run_network_poll
from app.services.settings_service import get_poll_interval_seconds

logger = logging.getLogger(__name__)

# Ключ в app.extensions: один планировщик на объект приложения.
SCHEDULER_EXT_KEY = "bawh_scheduler"
_POLL_COMMAND_KEY = "bawh_poll_command"
JOB_ID = "poll-devices"
ARCHIVE_JOB_ID = "archive-logs"
PASSWORD_EXPIRY_JOB_ID = "password-expiry"


def register_commands(app) -> None:
    """Команды CLI: `flask poll`, `flask archive-logs`, `flask password-expiry`.

    Повторный вызов ничего не делает. Иначе Click упадёт
    с «command already registered», когда create_app вызовут дважды
    (так устроены тесты).
    """
    if app.extensions.get(_POLL_COMMAND_KEY):
        return

    if "poll" not in app.cli.commands:

        @app.cli.command("poll")
        def poll() -> None:
            """Опросить все секторы один раз и напечатать сводку."""
            # flask уже открывает контекст, но опрос обязан работать и тогда,
            # когда команду вызовут не из CLI. Вложенный контекст это не ломает.
            with app.app_context():
                stats = run_network_poll(mode="cli")
                print(stats)

    if "archive-logs" not in app.cli.commands:

        @app.cli.command("archive-logs")
        def archive_logs_cmd() -> None:
            """Упаковать суточные логи прошлых месяцев в tar.gz."""
            with app.app_context():
                stats = _run_log_archive(app)
                print(stats)

    if "password-expiry" not in app.cli.commands:
        import click

        @app.cli.command("password-expiry")
        @click.option(
            "--dry-run",
            is_flag=True,
            help="Без SMTP и без записи истории уведомлений.",
        )
        def password_expiry_cmd(dry_run: bool) -> None:
            """Проверить сроки паролей AD и разослать уведомления."""
            with app.app_context():
                result = run_password_expiry(
                    send_emails=not dry_run,
                    mode="dry-run" if dry_run else "cli",
                )
                print(
                    {
                        "exit_code": result.exit_code,
                        "users": result.users_count,
                        "sent": result.sent_count,
                        "resolved": result.resolved_count,
                        "upcoming": result.upcoming_count,
                        "overdue": result.overdue_count,
                        "error": result.error,
                    }
                )

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

    Задача archive-logs — раз в сутки около 00:20 UTC: суточные хвосты
    завершённых месяцев пакуются в LOG_ARCHIVE_DIR.

    Задача password-expiry — cron из app_settings (локальный TZ сервера).

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
    password_cron_state = {"cron": "", "enabled": False, "func": None}

    def poll_job() -> None:
        # У планировщика нет HTTP-запроса, который открыл бы контекст сам.
        with app.app_context():
            try:
                run_network_poll(mode="scheduled")
            except PollInProgressError as exc:
                logger.warning("Плановый опрос пропущен: %s", exc)
            except Exception:
                logger.exception("Сбой планового опроса сети")
            finally:
                # Даже если опрос упал, интервал из формы должен примениться,
                # а соединение с БД — вернуться в пул.
                try:
                    db.session.rollback()
                    _apply_interval(scheduler, scheduled_for)
                    _apply_password_expiry_schedule(scheduler, password_cron_state)
                except Exception:
                    logger.exception("Не удалось обновить интервал опроса")
                finally:
                    db.session.remove()

    def archive_job() -> None:
        with app.app_context():
            try:
                _run_log_archive(app)
            except Exception:
                logger.exception("Не удалось архивировать логи")

    def password_expiry_job() -> None:
        with app.app_context():
            try:
                settings = get_password_expiry_settings()
                if not settings.schedule_enabled:
                    logger.info("Пароли AD: расписание выключено, пропуск")
                    return
                result = run_password_expiry(send_emails=True, mode="scheduled")
                logger.info(
                    "Пароли AD: scheduled exit=%s sent=%s users=%s",
                    result.exit_code,
                    result.sent_count,
                    result.users_count,
                )
            except Exception:
                logger.exception("Пароли AD: сбой плановой проверки")
            finally:
                db.session.remove()

    password_cron_state["func"] = password_expiry_job

    scheduler.add_job(
        poll_job,
        "interval",
        seconds=scheduled_for["seconds"],
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        archive_job,
        "cron",
        hour=0,
        minute=20,
        id=ARCHIVE_JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    with app.app_context():
        _apply_password_expiry_schedule(scheduler, password_cron_state, force=True)
    scheduler.start()
    app.extensions[SCHEDULER_EXT_KEY] = scheduler
    logger.info("Планировщик опроса запущен, интервал %s с", scheduled_for["seconds"])
    return scheduler


def _run_log_archive(app) -> dict[str, int]:
    log_file = app.config.get("LOG_FILE") or "logs/bawh.log"
    archive_dir = app.config.get("LOG_ARCHIVE_DIR") or "logs/archive"
    keep_months = int(app.config.get("LOG_ARCHIVE_KEEP_MONTHS") or 12)
    return archive_rotated_logs(
        log_file,
        archive_dir,
        keep_months=keep_months,
    )


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


def _parse_cron_trigger(expr: str) -> CronTrigger:
    parts = (expr or DEFAULT_SCHEDULE_CRON).split()
    if len(parts) != 5:
        parts = DEFAULT_SCHEDULE_CRON.split()
    minute, hour, day, month, day_of_week = parts
    local_tz = datetime.now().astimezone().tzinfo
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=local_tz,
    )


def _apply_password_expiry_schedule(
    scheduler: BackgroundScheduler,
    state: dict,
    *,
    force: bool = False,
) -> None:
    """Включить/выключить или переставить cron проверки паролей."""
    settings = get_password_expiry_settings()
    cron = settings.schedule_cron or DEFAULT_SCHEDULE_CRON
    enabled = bool(settings.schedule_enabled)
    if (
        not force
        and state.get("cron") == cron
        and state.get("enabled") == enabled
    ):
        return

    existing = scheduler.get_job(PASSWORD_EXPIRY_JOB_ID)
    if not enabled:
        if existing is not None:
            scheduler.remove_job(PASSWORD_EXPIRY_JOB_ID)
            logger.info("Пароли AD: расписание отключено")
        state["cron"] = cron
        state["enabled"] = False
        return

    job_func = state.get("func")
    trigger = _parse_cron_trigger(cron)
    if existing is None:
        if job_func is None:
            logger.warning("Пароли AD: нет функции задачи для регистрации")
            return
        scheduler.add_job(
            job_func,
            trigger=trigger,
            id=PASSWORD_EXPIRY_JOB_ID,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    else:
        scheduler.reschedule_job(PASSWORD_EXPIRY_JOB_ID, trigger=trigger)
    state["cron"] = cron
    state["enabled"] = True
    logger.info("Пароли AD: расписание cron=%s", cron)
