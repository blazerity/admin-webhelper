"""Справочник типов действий и лента событий системы.

Типы — таблица action_kinds.
События читаем из script_runs и device_account_history без отдельного event-log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.authz import accessible_sector_ids
from app.extensions import db
from app.models import (
    ActionKind,
    ActionKindCode,
    Device,
    DeviceAccountHistory,
    ScriptRun,
    seed_action_kinds,
)
from app.utils import as_utc

_UTC_MIN = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class SystemActionItem:
    """Единая строка ленты действий для UI."""

    kind_code: str
    kind_title: str
    title: str
    status: str
    when: datetime | None
    device_id: int | None
    device_label: str | None
    actor: str | None
    url: str | None


def ensure_action_kinds() -> dict[str, ActionKind]:
    """Гарантирует seed и возвращает словарь code → ActionKind."""
    seed_action_kinds()
    db.session.flush()
    rows = list(db.session.scalars(select(ActionKind)).all())
    return {row.code: row for row in rows}


def list_action_kinds() -> list[ActionKind]:
    """Справочник типов для UI, по названию."""
    return sorted(ensure_action_kinds().values(), key=lambda item: item.title)


def action_kind_title(kinds: dict[str, ActionKind], code: str) -> str:
    kind = kinds.get(code)
    if kind is not None:
        return kind.title
    return code


def _device_label(device: Device | None) -> str | None:
    if device is None:
        return None
    return device.hostname or device.ip


def _actor_label(user) -> str | None:
    if user is None:
        return None
    return user.display_name or user.username


def _run_title(run: ScriptRun, kinds: dict[str, ActionKind], code: str) -> str:
    if run.script is not None and run.script.name:
        return run.script.name
    text = " ".join((run.command_text or "").split())
    if len(text) > 72:
        text = text[:72] + "…"
    return text or action_kind_title(kinds, code)


def _item_sort_key(item: SystemActionItem):
    if item.when is None:
        return _UTC_MIN
    return as_utc(item.when)


def list_system_actions(user, *, limit: int = 50) -> list[SystemActionItem]:
    """Недавние действия: запуски на устройствах + появления УЗ.

    Из каждого журнала берём limit новейших (для двух источников этого
    достаточно, чтобы top-N после merge был хронологически точен), затем
    объединяем. Видимость запусков — как у scripts.run_detail.
    """
    from flask import url_for

    kinds = ensure_action_kinds()
    sector_ids = None if user.is_admin else accessible_sector_ids(user)

    run_stmt = (
        select(ScriptRun)
        .options(
            selectinload(ScriptRun.script),
            selectinload(ScriptRun.device),
            selectinload(ScriptRun.user),
        )
        .order_by(ScriptRun.started_at.desc(), ScriptRun.id.desc())
        .limit(limit)
    )
    if not user.is_admin:
        # Автор или устройство в доступном секторе — как user_can_see_script_run.
        # Orphaned runs (device удалён) видны только автору, не всем.
        run_filters = [ScriptRun.user_id == user.id]
        if sector_ids:
            run_filters.append(Device.sector_id.in_(sector_ids))
        run_stmt = run_stmt.outerjoin(Device, Device.id == ScriptRun.device_id).where(
            or_(*run_filters)
        )

    items: list[SystemActionItem] = []
    for run in db.session.scalars(run_stmt).all():
        # Запрос уже отфильтровал видимые запуски; повторный accessible_sectors
        # через user_can_see_script_run давал N+1 на каждой строке.
        if not user.is_admin and run.user_id != user.id:
            device = run.device
            if device is None or not sector_ids or device.sector_id not in sector_ids:
                continue
        code = run.run_type or ActionKindCode.COMMAND
        items.append(
            SystemActionItem(
                kind_code=code,
                kind_title=action_kind_title(kinds, code),
                title=_run_title(run, kinds, code),
                status=run.status,
                when=run.started_at,
                device_id=run.device_id,
                device_label=_device_label(run.device),
                actor=_actor_label(run.user),
                url=url_for("scripts.run_detail", run_id=run.id),
            )
        )

    sight_rows: list[DeviceAccountHistory] = []
    if sector_ids is None or sector_ids:
        sight_stmt = (
            select(DeviceAccountHistory)
            .options(
                selectinload(DeviceAccountHistory.account),
                selectinload(DeviceAccountHistory.device),
            )
            .order_by(DeviceAccountHistory.seen_at.desc(), DeviceAccountHistory.id.desc())
            .limit(limit)
        )
        if sector_ids is not None:
            sight_stmt = sight_stmt.join(
                Device, Device.id == DeviceAccountHistory.device_id
            ).where(Device.sector_id.in_(sector_ids))
        sight_rows = list(db.session.scalars(sight_stmt).all())

    code = ActionKindCode.ACCOUNT_SIGHTING
    for row in sight_rows:
        account_label = row.account.account_key if row.account else "—"
        items.append(
            SystemActionItem(
                kind_code=code,
                kind_title=action_kind_title(kinds, code),
                title=f"УЗ {account_label}",
                status="seen",
                when=row.seen_at,
                device_id=row.device_id,
                device_label=_device_label(row.device),
                actor=None,
                url=(
                    url_for("accounts.detail", account_id=row.account_id)
                    if row.account_id
                    else None
                ),
            )
        )

    items.sort(key=_item_sort_key, reverse=True)
    return items[:limit]
