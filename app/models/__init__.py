"""Импорт моделей, чтобы SQLAlchemy увидел таблицы до create_all."""

from app.models.account import DeviceAccountHistory, EndpointAccount, SessionType
from app.models.action import ACTION_KIND_SEED, ActionKind, ActionKindCode, seed_action_kinds
from app.models.audit import AdminAuditLog
from app.models.device import Device, DeviceAddress, DeviceHistory, DeviceStatus
from app.models.hardware import DeviceHardwareHistory, HardwarePollRun
from app.models.login_service import LoginService
from app.models.notification import Notification, NotificationKind
from app.models.password_expiry import PasswordExpiryRun, PasswordNotification
from app.models.poll_run import NetworkPollRun
from app.models.script import RunAs, RunStatus, RunType, Script, ScriptRun
from app.models.sector import Sector, SectorAccess, SectorRange
from app.models.sector_daily_report import SectorDailyReportRun
from app.models.setting import POLL_INTERVAL_KEY, AppSetting, RemoteCredential
from app.models.user import User, UserLdapGroup

__all__ = [
    "ACTION_KIND_SEED",
    "ActionKind",
    "ActionKindCode",
    "AdminAuditLog",
    "AppSetting",
    "Device",
    "DeviceAddress",
    "DeviceAccountHistory",
    "DeviceHardwareHistory",
    "DeviceHistory",
    "DeviceStatus",
    "HardwarePollRun",
    "EndpointAccount",
    "LoginService",
    "NetworkPollRun",
    "Notification",
    "NotificationKind",
    "PasswordExpiryRun",
    "PasswordNotification",
    "POLL_INTERVAL_KEY",
    "RemoteCredential",
    "RunAs",
    "RunStatus",
    "RunType",
    "Script",
    "ScriptRun",
    "Sector",
    "SectorAccess",
    "SectorDailyReportRun",
    "SectorRange",
    "SessionType",
    "User",
    "UserLdapGroup",
    "seed_action_kinds",
]
