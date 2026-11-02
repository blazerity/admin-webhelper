"""Отдельный процесс опроса сети (без HTTP).

Запуск: python -m app.scheduler_worker
Отчёты о паролях — отдельный процесс (password_report_worker).
"""

import logging

from app.process_main import run_scheduler_process
from app.services.scheduler_service import start_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    # wait=True внутри цикла: дать текущему пингу записать историю.
    run_scheduler_process(
        start_scheduler,
        started="Процесс опроса запущен. Веб-сервер в этом процессе не стартует.",
        stopping="Остановка планировщика, дожидаемся текущего прохода.",
        log=logger,
    )


if __name__ == "__main__":
    main()
