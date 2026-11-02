"""Общие куски прохода по уже известным карточкам devices.

Опрос железа и обновление имён ходят по одним полям (id, ip, hostname,
serial) и собирают ответы пулом потоков. Отбор целей и смысл пробы
у каждого прохода свои — их сюда не складываем.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSweepTarget:
    device_id: int
    ip: str
    hostname: str | None
    serial_number: str | None


def map_pool(items: Iterable, worker: Callable, *, workers: int, failure: str, log: logging.Logger) -> list:
    """Выполнить worker по каждому элементу. Сбой одного не роняет остальные."""
    results: list = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, item) for item in items]
        for future in futures:
            try:
                results.append(future.result())
            except Exception:
                log.exception("%s", failure)
    return results
