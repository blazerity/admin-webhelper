"""Поиск устройств по IP, MAC, имени и серийному номеру.

Маршрут только читает параметры и вызывает функции ниже.
Какие секторы видны пользователю, решает app.authz — здесь это не копируется.

Пустой запрос специально ничего не возвращает: шаблон «%» в LIKE
означает «любая строка», и без этой проверки поиск показал бы всю сеть.
"""

from flask import url_for
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app.authz import accessible_sector_ids
from app.models import Device
from app.utils import ilike_pattern


def search_devices(
    user, query: str, sector_id: int | None = None, limit: int = 50
) -> list[Device]:
    """Устройства пользователя, у которых IP, MAC, имя или серийник содержат query.

    sector_id сужает результат до одного сектора. Если этот сектор
    пользователю не выдан, возвращаем пустой список, а не чужие устройства.
    Регистр не учитывается. Порядок — по IP, не больше limit строк.
    Сектор уже подгружен (selectinload), шаблон читает device.sector.name.
    """
    text = (query or "").strip()
    if not text:
        return []

    allowed_ids = accessible_sector_ids(user)
    if sector_id is not None:
        if sector_id not in allowed_ids:
            return []
        sector_filter = Device.sector_id == sector_id
    elif not allowed_ids:
        return []
    else:
        sector_filter = Device.sector_id.in_(allowed_ids)

    pattern = ilike_pattern(text)
    return (
        Device.query.options(selectinload(Device.sector))
        .filter(sector_filter)
        .filter(
            or_(
                Device.ip.ilike(pattern, escape="\\"),
                Device.mac.ilike(pattern, escape="\\"),
                Device.hostname.ilike(pattern, escape="\\"),
                Device.serial_number.ilike(pattern, escape="\\"),
            )
        )
        .order_by(Device.ip)
        .limit(limit)
        .all()
    )


def _suggest_label(device: Device) -> str:
    """Текст подсказки: «10.0.0.5  hostname  SERIAL  AA:BB:...».

    Пустые поля пропускаем, чтобы не оставлять лишние пробелы.
    Между частями два пробела — так значения видно по отдельности.
    """
    parts = [device.ip]
    if device.hostname:
        parts.append(device.hostname)
    if device.serial_number:
        parts.append(device.serial_number)
    if device.mac:
        parts.append(device.mac)
    return "  ".join(parts)


def suggest_devices(user, query: str, limit: int = 8) -> list[dict]:
    """Короткие подсказки для поля поиска: список {label, url}.

    Пока введено меньше двух символов, в базу не ходим: один символ
    вроде «1» совпал бы с половиной адресов. url ведёт на карточку
    устройства. url_for работает, потому что функцию вызывает маршрут,
    уже внутри запроса.
    """
    text = (query or "").strip()
    if len(text) < 2:
        return []
    return [
        {
            "label": _suggest_label(device),
            "url": url_for("devices.detail", device_id=device.id),
        }
        for device in search_devices(user, text, limit=limit)
    ]
