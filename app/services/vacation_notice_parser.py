"""Разбор писем отдела кадров о предстоящем отпуске.

Ожидаемый фрагмент (текст или HTML):

    С 26.10.2026 планируется отпуск следующих сотрудников:
    Пользователь 1

Срок отпуска в письме не указан — его задают настройки модуля.
Несколько блоков «С ДД.ММ.ГГГГ…» в одном письме разбираются по отдельности.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Protocol


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


class NamedPerson(Protocol):
    username: str
    full_name: str


@dataclass(frozen=True)
class NameMatch:
    """Результат сопоставления строки письма с каталогом."""

    status: str  # matched | ambiguous | not_found
    username: str = ""
    full_name: str = ""
    candidates: tuple[str, ...] = ()


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


def normalize_person_name(value: str) -> str:
    """Схлопнуть пробелы и ё/е, чтобы «Иванов  Иван» совпал с displayName."""
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


def match_employee(name: str, people: list[NamedPerson]) -> NameMatch:
    """Сопоставить строку письма с учёткой AD.

    Сначала точное ФИО и логин, затем ФИО с начала карточки:
    «Иванов Иван» → «Иванов Иван Петрович», «Иванов И.И.» → инициалы.
    Несколько кандидатов не склеиваются в одну учётку.
    """
    key = normalize_person_name(name)
    if not key:
        return NameMatch(status="not_found")

    exact_full = [person for person in people if normalize_person_name(person.full_name) == key]
    if len(exact_full) == 1:
        return _matched(exact_full[0])
    if len(exact_full) > 1:
        return _ambiguous(exact_full)

    exact_login = [person for person in people if normalize_person_name(person.username) == key]
    if len(exact_login) == 1:
        return _matched(exact_login[0])
    if len(exact_login) > 1:
        return _ambiguous(exact_login)

    aligned = [person for person in people if _tokens_align(key, normalize_person_name(person.full_name))]
    if len(aligned) == 1:
        return _matched(aligned[0])
    if len(aligned) > 1:
        return _ambiguous(aligned)
    return NameMatch(status="not_found")


def _matched(person: NamedPerson) -> NameMatch:
    return NameMatch(
        status="matched",
        username=person.username,
        full_name=person.full_name,
        candidates=(person.username,),
    )


def _ambiguous(people: list[NamedPerson]) -> NameMatch:
    names = tuple(dict.fromkeys(person.username for person in people if person.username))
    return NameMatch(status="ambiguous", candidates=names)


def _tokens_align(notice: str, full_name: str) -> bool:
    """Слова письма совпадают с началом ФИО. Лишние слова в карточке допустимы."""
    notice_tokens = [token for token in notice.split() if token]
    name_tokens = [token for token in full_name.split() if token]
    if not notice_tokens or len(notice_tokens) > len(name_tokens):
        return False
    for index, token in enumerate(notice_tokens):
        if not _token_fits(token, name_tokens[index]):
            return False
    return True


def _token_fits(notice_token: str, name_token: str) -> bool:
    bare = notice_token.rstrip(".")
    if not bare:
        return False
    initial = notice_token.endswith(".") or len(bare) == 1
    if initial:
        return name_token.startswith(bare)
    return notice_token == name_token


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
