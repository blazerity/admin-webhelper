"""Отдельный процесс опроса сети/железа (без HTTP).

Запуск: python -m app.scheduler_worker
Отчёты о паролях и о ПК — отдельные процессы
(password_report_worker / pc_report_worker).
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
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Остановка планировщика, дожидаемся текущего прохода.")
        # wait=True: дать текущему пингу записать историю.
        scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
