"""Цикл процесса, в котором живёт только APScheduler.

bawh-scheduler и bawh-password-reports отличаются набором задач, не ожиданием.
Веб в этом процессе не стартует.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from app import create_app


def run_scheduler_process(
    start: Callable,
    *,
    started: str,
    stopping: str,
    log: logging.Logger,
) -> None:
    """Поднять приложение и планировщик, затем спать до Ctrl+C.

    shutdown(wait=True) даёт текущей задаче дописать журнал.
    """
    app = create_app()
    scheduler = start(app)
    log.info("%s", started)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("%s", stopping)
        scheduler.shutdown(wait=True)
