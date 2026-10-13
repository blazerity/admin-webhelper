"""Разбор писем отдела кадров о предстоящем отпуске.

В работу берётся только тема «Приближается отпуск сотрудников с ДД.ММ.ГГГГ».
В теле — один или несколько человек, полное ФИО построчно:

    С 26.10.2026 планируется отпуск следующих сотрудников:
    Посадских Евгений Александрович
    Родоманов Виктор Викторович

В AD записаны фамилия и имя (CN), без отчества: из письма берутся первые два слова.
Срок отпуска в письме не указан — его задают настройки модуля.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser


_SUBJECT_RE = re.compile(
    r"приближается\s+отпуск\s+сотрудников\s+с\s+\(?\s*"
    r"(\d{1,2})[./](\d{1,2})[./](\d{4})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"с\s+(\d{1,2})[./](\d{1,2})[./](\d{4})\s+планируется\s+отпуск",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"^(?:"
    r"с\s+уважением|"
    r"--\s*$|"
    r"это\s+(?:автоматическ|письмо)|"
    r"отправлено\s+из|"
    r"sent\s+from"
    r")",
    re.IGNORECASE,
)
_SKIP_RE = re.compile(
    r"^(?:"
    r"здравствуйте|"
    r"добрый\s+день|"
    r"доброе\s+утро|"
    r"уважаем"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VacationBlock:
    """Один список сотрудников с общей датой начала отпуска."""

    start: date
    names: list[str]


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        if tag in {"br", "p", "div", "tr", "li", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "tr", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html or "")
    parser.close()
    return parser.text()


def is_vacation_subject(subject: str) -> bool:
    """Тема кадрового письма: «Приближается отпуск сотрудников с ДД.ММ.ГГГГ»."""
    return _SUBJECT_RE.search(subject or "") is not None


def directory_short_name(full_name: str) -> str:
    """Полное ФИО из письма → фамилия и имя для CN.

    Отчество и всё, что после имени, в каталоге не хранится и в поиск не входит.
    «Посадских Евгений Александрович» → «Посадских Евгений».
    """
    tokens = [token for token in re.split(r"\s+", (full_name or "").strip()) if token]
    if len(tokens) >= 3:
        tokens = tokens[:2]
    return " ".join(tokens)


def normalize_person_name(value: str) -> str:
    """Схлопнуть пробелы и ё/е для сравнения CN с фамилией и именем."""
    text = (value or "").replace("ё", "е").replace("Ё", "Е")
    text = text.casefold()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    # «И.И.» и «И.И» — два инициала, а не одно слово.
    text = re.sub(r"(?<=\w)\.(?=\w)", ". ", text)
    text = re.sub(r"\s+", " ", text).strip(" .\t")
    return text


def parse_vacation_notice(text: str) -> list[VacationBlock]:
    """Достать даты начала и ФИО. Пустой список — письмо не похоже на уведомление."""
    lines = [_clean_line(line) for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    blocks: list[VacationBlock] = []
    current_start: date | None = None
    current_names: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_names
        if current_start is not None:
            blocks.append(VacationBlock(start=current_start, names=list(current_names)))
        current_start = None
        current_names = []

    for line in lines:
        if not line:
            if current_names:
                flush()
            continue
        found = _DATE_RE.search(line)
        if found:
            flush()
            day, month, year = (int(found.group(1)), int(found.group(2)), int(found.group(3)))
            try:
                current_start = date(year, month, day)
            except ValueError:
                current_start = None
            current_names = []
            continue
        if current_start is None:
            continue
        if _STOP_RE.search(line) or _SKIP_RE.search(line):
            flush()
            continue
        if not _looks_like_name(line):
            continue
        current_names.append(line)

    flush()
    return [block for block in blocks if block.names]


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", (line or "").replace("\xa0", " ")).strip()


def _looks_like_name(line: str) -> bool:
    if len(line) < 2 or len(line) > 120:
        return False
    lowered = line.casefold()
    if "@" in line or lowered.startswith("http") or "://" in lowered:
        return False
    if not re.search(r"[\w]", line, flags=re.UNICODE):
        return False
    return True
