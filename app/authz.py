"""Кто какой сектор и устройство видит.

Админ видит всё; остальные — по sector_access на username или LDAP-группы.
При тысячах секторов accessible_sector_ids стоит заменить на EXISTS / кэш.
"""

from functools import wraps

from flask import abort
from flask_login import current_user, login_required
from sqlalchemy import and_, func, or_

from app.extensions import db
from app.models import Device, Sector, SectorAccess


def admin_required(view):
    """Пускает только администратора. Сначала требует вход."""

    @login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def accessible_sectors_query(user):
    if user.is_admin:
        return Sector.query
    username = (user.username or "").lower()
    group_names = [g.group_name.lower() for g in user.ldap_groups if g.group_name]
    conditions = [
        and_(
            SectorAccess.subject_type == "user",
            func.lower(SectorAccess.subject_name) == username,
        )
    ]
    if group_names:
        conditions.append(
            and_(
                SectorAccess.subject_type == "group",
                func.lower(SectorAccess.subject_name).in_(group_names),
            )
        )
    return (
        Sector.query.join(Sector.access_rules)
        .filter(or_(*conditions))
        .distinct()
    )


def accessible_sectors(user) -> list[Sector]:
    return accessible_sectors_query(user).order_by(Sector.name).all()


def accessible_sector_ids(user) -> set[int]:
    return {sector.id for sector in accessible_sectors(user)}


def user_can_access_sector(user, sector: Sector | None) -> bool:
    if sector is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_admin:
        return True
    return sector.id in accessible_sector_ids(user)


def user_can_access_device(user, device: Device | None) -> bool:
    if device is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_admin:
        return True
    if device.sector_id is None:
        return False
    return device.sector_id in accessible_sector_ids(user)


def get_visible_sector_or_404(sector_id: int) -> Sector:
    sector = db.session.get(Sector, sector_id)
    if sector is None:
        abort(404)
    if not user_can_access_sector(current_user, sector):
        abort(403)
    return sector


def get_visible_device_or_404(device_id: int) -> Device:
    device = db.session.get(Device, device_id)
    if device is None:
        abort(404)
    if not user_can_access_device(current_user, device):
        abort(403)
    return device
