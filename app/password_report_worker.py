"""Отдельный процесс рассылки отчётов о паролях AD и проверки перед отпуском (без HTTP).

Запуск: python -m app.password_report_worker
"""

import logging

from app.process_main import run_scheduler_process
from app.services.scheduler_service import start_password_report_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    run_scheduler_process(
        start_password_report_scheduler,
        started=(
            "Процесс отчётов о паролях AD запущен. "
            "Веб-сервер в этом процессе не стартует."
        ),
        stopping="Остановка планировщика паролей AD.",
        log=logger,
    )


if __name__ == "__main__":
    main()
