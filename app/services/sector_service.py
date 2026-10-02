"""Создание, правка и удаление секторов.

Маршруты только читают форму и вызывают эти функции.
Проверка IP/CIDR живёт в net_utils: здесь та же функция,
что и у будущего опроса, поэтому «10.0.0.0/8» и мусор
отклоняются одинаково.

save_sector либо записывает сектор целиком и делает commit,
либо поднимает ошибку и откатывает сессию. Частичной строки
в базе не остаётся: имя, диапазоны и права проверяются
до того, как старые строки удаляются.
"""

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Sector, SectorAccess, SectorRange
from app.services.net_utils import parse_range, range_to_text

_NAME_MAX = 128
_SUBJECT_MAX = 255


class SectorError(ValueError):
    """Ошибка данных сектора. Текст уже можно показать в flash."""


def save_sector(
    sector_id: int | None,
    name: str,
    description: str,
    ranges_text: str,
    access_users: str,
    access_groups: str,
) -> Sector:
    """Создать сектор (sector_id is None) или обновить существующий.

    ranges_text — по одному IP или CIDR на строку, пустые строки
    пропускаются. Каждая строка проходит parse_range, в базу пишется
    range_to_text. Нужен хотя бы один диапазон.

    access_users и access_groups — имена через запятую.
    Пустые куски отбрасываются, пробелы по краям снимаются.
    Старые диапазоны и права заменяются. Устройства не удаляются.
    """
    clean_name = _clean_name(name)
    description_text = description or ""
    cidrs = _parse_ranges(ranges_text)
    users = _split_subjects(access_users)
    groups = _split_subjects(access_groups)
    _ensure_unique_name(clean_name, sector_id)

    try:
        sector = _get_or_create(sector_id, clean_name, description_text)
        sector.name = clean_name
        sector.description = description_text
        _replace_ranges_and_access(sector, cidrs, users, groups)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise SectorError("Сектор с таким названием уже есть.") from exc
    except Exception:
        db.session.rollback()
        raise
    return sector


def delete_sector(sector_id: int) -> None:
    """Удалить сектор и зафиксировать транзакцию.

    Каскад модели снимает диапазоны, права, устройства и их историю.
    Если сектора нет, данные не меняются.
    """
    sector = db.session.get(Sector, sector_id)
    if sector is None:
        raise SectorError("Сектор не найден.")
    db.session.delete(sector)
    db.session.commit()


def _clean_name(name: str) -> str:
    clean = (name or "").strip()
    if not clean:
        raise SectorError("Укажите название сектора.")
    if len(clean) > _NAME_MAX:
        raise SectorError("Название не длиннее 128 символов.")
    return clean


def _ensure_unique_name(name: str, sector_id: int | None) -> None:
    # Сравнение как в базе: регистр важен, «Склад» и «склад» — разные секторы.
    conflict = Sector.query.filter_by(name=name).first()
    if conflict is not None and conflict.id != sector_id:
        raise SectorError("Сектор с таким названием уже есть.")


def _parse_ranges(ranges_text: str) -> list[str]:
    cidrs: list[str] = []
    for line in (ranges_text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        # NetworkInputError не прячем: в форме нужен текст про префикс или мусор.
        cidrs.append(range_to_text(parse_range(raw)))
    if not cidrs:
        raise SectorError("Укажите хотя бы один IP или CIDR.")
    return cidrs


def _split_subjects(text: str) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for part in (text or "").split(","):
        subject = part.strip()
        if not subject or subject in seen:
            continue
        if len(subject) > _SUBJECT_MAX:
            raise SectorError("Имя пользователя или группы длиннее 255 символов.")
        seen.add(subject)
        names.append(subject)
    return names


def _get_or_create(sector_id: int | None, name: str, description: str) -> Sector:
    if sector_id is None:
        sector = Sector(name=name, description=description)
        db.session.add(sector)
        return sector
    sector = db.session.get(Sector, sector_id)
    if sector is None:
        raise SectorError("Сектор не найден.")
    return sector


def _replace_ranges_and_access(
    sector: Sector,
    cidrs: list[str],
    users: list[str],
    groups: list[str],
) -> None:
    """Удалить прежние диапазоны и права и записать новый набор.

    Устройства (sector.devices) здесь не трогаем: смена CIDR
    не должна стирать уже найденные хосты.
    """
    sector.ranges.clear()
    sector.access_rules.clear()
    # flush до INSERT: иначе повтор того же логина упирается
    # в уникальность (sector_id, subject_type, subject_name),
    # потому что старая строка ещё не удалена.
    db.session.flush()
    for cidr in cidrs:
        sector.ranges.append(SectorRange(cidr=cidr))
    for subject_name in users:
        sector.access_rules.append(
            SectorAccess(subject_type="user", subject_name=subject_name)
        )
    for subject_name in groups:
        sector.access_rules.append(
            SectorAccess(subject_type="group", subject_name=subject_name)
        )
