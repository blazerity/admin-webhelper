"""Логи приложения: файл + stdout.

stdout забирает journald, когда процесс запущен как systemd-сервис
(`journalctl -u bawh-web`). Файл нужен, если смотреть историю без journalctl
или запускать приложение вручную.

Формат один и тот же, чтобы строки из файла и из журнала читались одинаково.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask


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
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    log_file = app.config.get("LOG_FILE")
    if not log_file:
        return

    log_dir = os.path.dirname(log_file)
    try:
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
    except OSError:
        # Каталог недоступен (например, в ограниченном тесте) — остаётся stdout.
        logging.getLogger(__name__).warning("Не удалось открыть файл лога %s", log_file)
