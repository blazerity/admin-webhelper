"""Пресеты удалённых команд для карточки устройства.

Источник истины для UI (SSR context) и опционального JSON API.
Разметка кнопок — у A2; здесь только данные.
"""

from __future__ import annotations

from typing import TypedDict


class CommandPreset(TypedDict):
    id: str
    label: str
    command: str


COMMAND_PRESETS: tuple[CommandPreset, ...] = (
    {"id": "whoami", "label": "whoami", "command": "whoami"},
    {"id": "ipconfig", "label": "ipconfig /all", "command": "ipconfig /all"},
    {"id": "hostname", "label": "hostname", "command": "hostname"},
    {"id": "netstat", "label": "netstat -ano", "command": "netstat -ano"},
)


def list_command_presets() -> list[CommandPreset]:
    """Копия списка пресетов для context / JSON."""
    return [dict(item) for item in COMMAND_PRESETS]
