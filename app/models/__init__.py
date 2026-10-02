"""Импорт моделей, чтобы SQLAlchemy увидел таблицы до create_all / Alembic."""

from app.models.account import DeviceAccountHistory, EndpointAccount, SessionType
from app.models.action import ACTION_KIND_SEED, ActionKind, ActionKindCode, seed_action_kinds
from app.models.device import Device, DeviceHistory, DeviceStatus
from app.models.script import RunAs, RunStatus, RunType, Script, ScriptRun
from app.models.sector import Sector, SectorAccess, SectorRange
from app.models.setting import POLL_INTERVAL_KEY, AppSetting, RemoteCredential
from app.models.user import User, UserLdapGroup

__all__ = [
    "ACTION_KIND_SEED",
    "ActionKind",
    "ActionKindCode",
    "AppSetting",
    "Device",
    "DeviceAccountHistory",
    "DeviceHistory",
    "DeviceStatus",
    "EndpointAccount",
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
    "SessionType",
    "User",
    "UserLdapGroup",
    "seed_action_kinds",
]
