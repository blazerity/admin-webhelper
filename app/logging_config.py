"""Логи приложения: файл + stdout.

stdout забирает journald, когда процесс запущен как systemd-сервис
(`journalctl -u bawh-web`). Файл нужен, если смотреть историю без journalctl
или запускать приложение вручную.

Ротация файла — раз в сутки около полуночи (TimedRotatingFileHandler).
Суточные хвосты оставляет на диске; раз в месяц их пакует
`log_archive_service` в `LOG_ARCHIVE_DIR`.

Формат один и тот же, чтобы строки из файла и из журнала читались одинаково.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from flask import Flask

# Сторонние логгеры на INFO заливают файл каждые несколько минут
# (APScheduler) или на каждый HTTP-запрос (werkzeug в flask run).
_QUIET_LOGGERS = (
    "apscheduler",
    "apscheduler.scheduler",
    "apscheduler.executors.default",
    "werkzeug",
    "urllib3",
    "paramiko",
    "smbprotocol",
    "spnego",
)


def configure_logging(app: Flask) -> None:
    level_name = str(app.config.get("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # При повторном create_app() в тестах не вешаем обработчики пачкой.
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, TimedRotatingFileHandler)
        for h in root.handlers
    ):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    log_file = app.config.get("LOG_FILE")
    if not log_file:
        return

    log_dir = os.path.dirname(log_file)
    try:
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers):
            # backupCount=0: суточные файлы не удаляем здесь —
            # retention делает archive_rotated_logs (месячные tar.gz).
            file_handler = TimedRotatingFileHandler(
                log_file,
                when="midnight",
                interval=1,
                backupCount=0,
                encoding="utf-8",
            )
            file_handler.suffix = "%Y-%m-%d"
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
    except OSError:
        # Каталог недоступен (например, в ограниченном тесте) — остаётся stdout.
        logging.getLogger(__name__).warning("Не удалось открыть файл лога %s", log_file)
