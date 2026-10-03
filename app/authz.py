"""Кто какой сектор, устройство и запуск видит.

Админ видит всё; остальные — по sector_access (username / LDAP-группа)
и правилу для script_runs: автор или доступное устройство.
"""

from functools import wraps

from flask import abort
from flask_login import current_user, login_required
from sqlalchemy import and_, func, or_

from app.extensions import db
from app.models import Device, Sector, SectorAccess, ScriptRun


def admin_required(view):
    """Пускает только администратора. Сначала требует вход."""

    @login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def user_can_run_scripts(user) -> bool:
    """Может ли пользователь запускать/управлять скриптами библиотеки.

    W1: только admin. W3 расширит для operator
    (опубликованные скрипты на доступных секторах) — см. docs/adr/002-authz-v2.md.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_admin)


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


def user_can_see_script_run(user, run: ScriptRun | None) -> bool:
    """Админ; автор запуска; либо устройство запуска доступно пользователю."""
    if run is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    if run.user_id is not None and run.user_id == user.id:
        return True
    return user_can_access_device(user, run.device)


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


def get_visible_script_run_or_404(run_id: int) -> ScriptRun:
    run = db.session.get(ScriptRun, run_id)
    if run is None:
        abort(404)
    if not user_can_see_script_run(current_user, run):
        abort(403)
    return run
