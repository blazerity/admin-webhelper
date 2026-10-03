"""Кто какой сектор, устройство и запуск видит.

Админ видит всё; остальные — по sector_access (username / LDAP-группа)
и правилу для script_runs: автор или доступное устройство.

Authz v2 (W3): additive роли viewer / operator / password_viewer поверх
is_admin + sector_access. См. docs/adr/002-authz-v2.md.
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


def user_has_role(user, role: str) -> bool:
    """Есть ли у пользователя роль. ``admin`` подразумевает все роли."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    key = (role or "").strip().lower()
    if key == "admin":
        return False
    if key == "viewer":
        return bool(getattr(user, "is_viewer", False))
    if key == "operator":
        return bool(getattr(user, "is_operator", False))
    if key == "password_viewer":
        return bool(getattr(user, "is_password_viewer", False))
    return False


def user_can_run_scripts(user) -> bool:
    """Может ли пользователь запускать скрипты библиотеки.

    Admin или operator. CRUD скриптов по-прежнему только через admin_required.
    """
    return user_has_role(user, "operator")


def user_can_run_script(user, script) -> bool:
    """Может ли пользователь запустить конкретный скрипт.

    Admin — любой; operator — только ``script.is_published``.
    """
    if script is None or not user_can_run_scripts(user):
        return False
    if getattr(user, "is_admin", False):
        return True
    return bool(getattr(script, "is_published", False))


def user_can_view_password_expiry(user) -> bool:
    """Отчёт паролей AD: admin или password_viewer (settings — отдельно admin)."""
    return user_has_role(user, "password_viewer")


def user_can_run_diagnostics(user) -> bool:
    """Ping / tracert на доступных устройствах.

    Admin и operator — да. Чистый viewer (без operator/admin) — нет.
    Пользователь только с sector_access без новых ролей — как раньше (да).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False) or getattr(user, "is_operator", False):
        return True
    if getattr(user, "is_viewer", False):
        return False
    return True


def user_can_bulk_ping(user) -> bool:
    """Может ли пользователь запускать bulk ping (W2/W3).

    Те же правила, что у diagnostics; каждое устройство всё равно
    фильтруется через ``filter_accessible_devices``.
    """
    return user_can_run_diagnostics(user)


def user_can_bulk_script(user) -> bool:
    """Может ли пользователь запускать bulk script.

    Обёртка над ``user_can_run_scripts``; конкретный скрипт проверяется
    через ``user_can_run_script`` на маршруте.
    """
    return user_can_run_scripts(user)


def filter_accessible_devices(user, device_ids) -> list[Device]:
    """Вернуть доступные устройства в порядке ``device_ids``.

    Пропускает отсутствующие id и устройства вне ACL пользователя.
    Не дублирует: повтор id во входе даёт повтор в выходе, если доступен.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    if not device_ids:
        return []

    ids = [int(device_id) for device_id in device_ids]
    unique_ids = set(ids)
    devices_by_id = {
        device.id: device
        for device in Device.query.filter(Device.id.in_(unique_ids)).all()
    }

    if getattr(user, "is_admin", False):
        return [devices_by_id[device_id] for device_id in ids if device_id in devices_by_id]

    # Один ACL-запрос секторов на весь bulk, без N× accessible_sector_ids.
    sector_ids = accessible_sector_ids(user)
    result: list[Device] = []
    for device_id in ids:
        device = devices_by_id.get(device_id)
        if device is not None and device.sector_id is not None and device.sector_id in sector_ids:
            result.append(device)
    return result


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


def accessible_devices(user) -> list[Device]:
    """Устройства в доступных секторах (для селектов форм)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    if getattr(user, "is_admin", False):
        return Device.query.order_by(Device.ip).all()
    sector_ids = accessible_sector_ids(user)
    if not sector_ids:
        return []
    return (
        Device.query.filter(Device.sector_id.in_(sector_ids))
        .order_by(Device.ip)
        .all()
    )


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
