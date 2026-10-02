"""Номер версии приложения.

Источник правды — файл VERSION в корне репозитория.
При выпуске правьте только его (semver: MAJOR.MINOR.PATCH), коммитьте и пушьте.
Страница обновлений показывает этот номер вместо хеша коммита.
"""

from __future__ import annotations

import re
from pathlib import Path

# app/version.py → корень репозитория
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?([+-][0-9A-Za-z.-]+)?$")


def parse_version(raw: str) -> str:
    """Вернуть нормализованный номер или пустую строку."""
    text = (raw or "").strip().splitlines()[0].strip() if raw else ""
    if text.lower().startswith("v") and len(text) > 1:
        text = text[1:]
    if _VERSION_RE.fullmatch(text):
        return text
    return ""


def read_version_file(root: Path) -> str:
    """Прочитать VERSION из указанного каталога проекта."""
    path = Path(root) / "VERSION"
    if not path.is_file():
        return ""
    try:
        return parse_version(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def get_version() -> str:
    """Версия текущего кода из дерева приложения."""
    return read_version_file(_PACKAGE_ROOT) or "0.0.0"
