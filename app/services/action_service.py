"""Справочник типов действий и лента событий системы.

Типы — таблица action_kinds.
События пока читаем из script_runs (удалённые запуски) и
device_account_history (обнаружения УЗ): отдельный event-log не дублируем.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
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


def action_kind_title(kinds: dict[str, ActionKind], code: str) -> str:
    kind = kinds.get(code)
    if kind is not None:
        return kind.title
    return code


def list_system_actions(user, *, limit: int = 50) -> list[SystemActionItem]:
    """Недавние действия: запуски на устройствах + появления УЗ."""
    from flask import url_for

    kinds = ensure_action_kinds()
    sector_ids = None if user.is_admin else accessible_sector_ids(user)
    if sector_ids is not None and not sector_ids:
        return []

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
    if sector_ids is not None:
        run_stmt = run_stmt.outerjoin(Device, Device.id == ScriptRun.device_id).where(
            (ScriptRun.device_id.is_(None)) | (Device.sector_id.in_(sector_ids))
        )

    items: list[SystemActionItem] = []
    for run in db.session.scalars(run_stmt).all():
        code = run.run_type or ActionKindCode.COMMAND
        if run.script is not None and run.script.name:
            title = run.script.name
        else:
            text = " ".join((run.command_text or "").split())
            if len(text) > 72:
                text = text[:72] + "…"
            title = text or action_kind_title(kinds, code)
        device_label = None
        if run.device is not None:
            device_label = run.device.hostname or run.device.ip
        actor = None
        if run.user is not None:
            actor = run.user.display_name or run.user.username
        items.append(
            SystemActionItem(
                kind_code=code,
                kind_title=action_kind_title(kinds, code),
                title=title,
                status=run.status,
                when=run.started_at,
                device_id=run.device_id,
                device_label=device_label,
                actor=actor,
                url=url_for("scripts.run_detail", run_id=run.id),
            )
        )

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
        sight_stmt = sight_stmt.join(Device, Device.id == DeviceAccountHistory.device_id).where(
            Device.sector_id.in_(sector_ids)
        )

    code = ActionKindCode.ACCOUNT_SIGHTING
    for row in db.session.scalars(sight_stmt).all():
        account_label = row.account.account_key if row.account else "—"
        device_label = None
        if row.device is not None:
            device_label = row.device.hostname or row.device.ip
        items.append(
            SystemActionItem(
                kind_code=code,
                kind_title=action_kind_title(kinds, code),
                title=f"УЗ {account_label}",
                status="seen",
                when=row.seen_at,
                device_id=row.device_id,
                device_label=device_label,
                actor=None,
                url=(
                    url_for("accounts.detail", account_id=row.account_id)
                    if row.account_id
                    else None
                ),
            )
        )

    items.sort(key=lambda item: item.when or datetime.min, reverse=True)
    return items[:limit]
