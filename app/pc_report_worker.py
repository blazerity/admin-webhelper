"""Отдельный процесс рассылки отчётов о ПК (без HTTP).

Запуск: python -m app.pc_report_worker
"""

import logging
import time

from app import create_app
from app.services.scheduler_service import start_pc_report_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    app = create_app()
    scheduler = start_pc_report_scheduler(app)
    logger.info(
        "Процесс отчётов о ПК запущен. Веб-сервер в этом процессе не стартует."
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Остановка планировщика отчётов о ПК.")
        scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
