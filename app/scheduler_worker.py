"""Отдельный процесс опроса. HTTP здесь не поднимается.

Запуск из корня проекта (там, где лежит пакет app):

    python -m app.scheduler_worker

Gunicorn этот модуль не импортирует. Веб отдаёт страницы, а пинг
идёт здесь: один процесс, один планировщик, одна запись истории.
Остановка — Ctrl+C: цикл сна получит KeyboardInterrupt и вызовет
scheduler.shutdown().

Главный поток почти ничего не делает. Задачу по таймеру выполняет
поток APScheduler, который start_scheduler уже запустил.
"""

import logging
import time

from app import create_app
from app.services.scheduler_service import start_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    app = create_app()
    scheduler = start_scheduler(app)
    logger.info("Процесс опроса запущен. Веб-сервер в этом процессе не стартует.")
    try:
        # sleep отдаёт процессор. Прерывание с клавиатуры выходит из sleep.
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Остановка планировщика, дожидаемся текущего прохода.")
        # wait=True: если пинг уже идёт, даём ему записать историю и выйти.
        scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
