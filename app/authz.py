"""Кто видит сектора, устройства и запуски.

Отдельных ролей нет. Вошедший пользователь видит всю сеть и может
выполнять любые действия. Вход в систему решает группа AD
(LDAP_ADMIN_GROUP) в ldap_service.
"""

from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Device, Sector, ScriptRun


def _signed_in(user) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))


def admin_required(view):
    """Пускает любого вошедшего. Имя декоратора оставлено у настроек и мутаций."""

    @login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        return view(*args, **kwargs)

    return wrapper


def user_can_run_scripts(user) -> bool:
    """Запуск и библиотека скриптов доступны каждому вошедшему."""
    return _signed_in(user)


def user_can_run_script(user, script) -> bool:
    """Конкретный скрипт можно запустить, если пользователь вошёл."""
    return script is not None and _signed_in(user)


def user_can_view_password_expiry(user) -> bool:
    """Отчёт паролей AD открыт каждому вошедшему."""
    return _signed_in(user)


def user_can_run_diagnostics(user) -> bool:
    """Ping, tracert и команды — каждому вошедшему."""
    return _signed_in(user)


def user_can_connect_vnc(user) -> bool:
    """Веб-VNC: вошедший пользователь и включённый тумблер в Настройках → Общие."""
    if not user_can_run_diagnostics(user):
        return False
    from app.services.vnc_settings import is_vnc_enabled

    return is_vnc_enabled()


def filter_accessible_devices(user, device_ids) -> list[Device]:
    """Вернуть существующие устройства в порядке ``device_ids``.

    Пропускает отсутствующие id. Повтор id во входе даёт повтор в выходе.
    """
    if not _signed_in(user) or not device_ids:
        return []

    ids = [int(device_id) for device_id in device_ids]
    devices_by_id = {
        device.id: device
        for device in Device.query.filter(Device.id.in_(set(ids))).all()
    }
    return [devices_by_id[device_id] for device_id in ids if device_id in devices_by_id]


def accessible_sectors_query(user):
    if not _signed_in(user):
        return Sector.query.filter(Sector.id.is_(None))
    return Sector.query


def accessible_sectors(user) -> list[Sector]:
    return accessible_sectors_query(user).order_by(Sector.name).all()


def accessible_sector_ids(user) -> set[int]:
    return {sector.id for sector in accessible_sectors(user)}


def accessible_devices(user) -> list[Device]:
    """Все устройства (для селектов форм)."""
    if not _signed_in(user):
        return []
    return Device.query.order_by(Device.ip).all()


def user_can_access_sector(user, sector: Sector | None) -> bool:
    return sector is not None and _signed_in(user)


def user_can_access_device(user, device: Device | None) -> bool:
    return device is not None and _signed_in(user)


def user_can_see_script_run(user, run: ScriptRun | None) -> bool:
    return run is not None and _signed_in(user)


def get_visible_sector_or_404(sector_id: int) -> Sector:
    sector = db.session.get(Sector, sector_id)
    if sector is None or not user_can_access_sector(current_user, sector):
        abort(404)
    return sector


def get_visible_device_or_404(device_id: int) -> Device:
    device = db.session.get(Device, device_id)
    if device is None or not user_can_access_device(current_user, device):
        abort(404)
    return device


def get_visible_script_run_or_404(run_id: int) -> ScriptRun:
    run = db.session.get(ScriptRun, run_id)
    if run is None or not user_can_see_script_run(current_user, run):
        abort(404)
    return run
