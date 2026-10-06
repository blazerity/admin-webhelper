"""Подписи запусков ScriptRun для UI (карточка устройства, лог, лента)."""

from __future__ import annotations

from datetime import datetime

from app.models import RunStatus, RunType, ScriptRun
from app.utils import format_utc

RUN_STATUS_LABELS = {
    RunStatus.PENDING: "ожидание",
    RunStatus.RUNNING: "выполняется",
    RunStatus.SUCCESS: "успешно",
    RunStatus.FAILED: "ошибка",
    RunStatus.CANCELLED: "остановлен",
}

RUN_TYPE_LABELS = {
    RunType.PING: "Ping",
    RunType.TRACERT: "Трассировка",
    RunType.COMMAND: "Команда",
    RunType.SCRIPT: "Скрипт",
    RunType.VNC_ENSURE: "Агент VNC",
}


def run_status_label(status: str | None) -> str:
    if not status:
        return "—"
    return RUN_STATUS_LABELS.get(status, status)


def run_type_label(run_type: str | None) -> str:
    if not run_type:
        return "—"
    return RUN_TYPE_LABELS.get(run_type, run_type)


def run_when_label(value: datetime | None) -> str:
    return format_utc(value)


def run_launch_label(item: ScriptRun) -> str:
    """Имя скрипта или «Тип: команда» для истории запусков."""
    if item.script is not None and item.script.name:
        return item.script.name
    text = " ".join((item.command_text or "").split())
    if len(text) > 72:
        text = text[:72] + "…"
    kind = run_type_label(item.run_type)
    if text:
        return f"{kind}: {text}"
    return kind
