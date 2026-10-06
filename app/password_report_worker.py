"""Отдельный процесс рассылки отчётов о паролях AD (без HTTP).

Запуск: python -m app.password_report_worker
"""

import logging
import time

from app import create_app
from app.services.scheduler_service import start_password_report_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    app = create_app()
    scheduler = start_password_report_scheduler(app)
    logger.info(
        "Процесс отчётов о паролях AD запущен. Веб-сервер в этом процессе не стартует."
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Остановка планировщика паролей AD.")
        scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
