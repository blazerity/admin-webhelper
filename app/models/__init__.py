"""Импорт моделей, чтобы SQLAlchemy увидел таблицы до create_all / Alembic."""

from app.models.device import Device, DeviceHistory, DeviceStatus
from app.models.script import RunAs, RunStatus, RunType, Script, ScriptRun
from app.models.sector import Sector, SectorAccess, SectorRange
from app.models.setting import POLL_INTERVAL_KEY, AppSetting, RemoteCredential
from app.models.user import User, UserLdapGroup

__all__ = [
    "AppSetting",
    "Device",
    "DeviceHistory",
    "DeviceStatus",
    "POLL_INTERVAL_KEY",
    "RemoteCredential",
    "RunAs",
    "RunStatus",
    "RunType",
    "Script",
    "ScriptRun",
    "Sector",
    "SectorAccess",
    "SectorRange",
    "User",
    "UserLdapGroup",
]
