"""Справочник типов действий и лента событий системы.

Типы — таблица action_kinds.
События читаем из script_runs и device_account_history без отдельного event-log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    ActionKind,
    ActionKindCode,
    Device,
    DeviceAccountHistory,
    ScriptRun,
    seed_action_kinds,
)
from app.services.script_service import command_text_body
from app.utils import as_utc

_UTC_MIN = datetime.min.replace(tzinfo=timezone.utc)
_MAX_FETCH = 1000


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
    text = " ".join(command_text_body(run.command_text or "").split())
    if len(text) > 72:
        text = text[:72] + "…"
    return text or action_kind_title(kinds, code)


def _item_sort_key(item: SystemActionItem):
    if item.when is None:
        return _UTC_MIN
    return as_utc(item.when)


def list_system_actions(
    user,
    *,
    page: int = 1,
    per_page: int = 50,
    q: str = "",
    kind: str = "",
    limit: int | None = None,
) -> dict:
    """Недавние действия с пагинацией и фильтрами.

    Возвращает ``{items, total, page, per_page}``.
    ``limit`` — совместимость со старыми вызовами (page=1, per_page=limit).
    """
    from flask import url_for

    from app.utils import normalize_page

    if limit is not None:
        page, per_page = 1, max(1, int(limit))
    page, per_page = normalize_page(page, per_page)
    if user is None or not getattr(user, "is_authenticated", False):
        return {"items": [], "total": 0, "page": page, "per_page": per_page}
    query_text = (q or "").strip().lower()
    kind_code = (kind or "").strip().lower()

    kinds = ensure_action_kinds()

    run_stmt = (
        select(ScriptRun)
        .options(
            selectinload(ScriptRun.script),
            selectinload(ScriptRun.device),
            selectinload(ScriptRun.user),
        )
        .order_by(ScriptRun.started_at.desc(), ScriptRun.id.desc())
        .limit(_MAX_FETCH)
    )

    items: list[SystemActionItem] = []
    for run in db.session.scalars(run_stmt).all():
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

    sight_stmt = (
        select(DeviceAccountHistory)
        .options(
            selectinload(DeviceAccountHistory.account),
            selectinload(DeviceAccountHistory.device),
        )
        .order_by(DeviceAccountHistory.seen_at.desc(), DeviceAccountHistory.id.desc())
        .limit(_MAX_FETCH)
    )
    sight_rows = list(db.session.scalars(sight_stmt).all())

    # Одно событие на пару УЗ+устройство (последнее обнаружение), иначе лента
    # забивается почасовыми повторными sighting'ами одного и того же пользователя.
    code = ActionKindCode.ACCOUNT_SIGHTING
    sight_counts: dict[tuple[int | None, int | None], int] = {}
    latest_sights: list[DeviceAccountHistory] = []
    for row in sight_rows:
        key = (row.account_id, row.device_id)
        sight_counts[key] = sight_counts.get(key, 0) + 1
        if sight_counts[key] == 1:
            latest_sights.append(row)

    for row in latest_sights:
        account_label = row.account.account_key if row.account else "—"
        repeats = sight_counts.get((row.account_id, row.device_id), 1)
        title = f"УЗ {account_label}"
        if repeats > 1:
            title = f"{title} · ещё {repeats - 1}"
        items.append(
            SystemActionItem(
                kind_code=code,
                kind_title=action_kind_title(kinds, code),
                title=title,
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

    if kind_code:
        items = [item for item in items if (item.kind_code or "").lower() == kind_code]
    if query_text:
        items = [
            item
            for item in items
            if query_text in (item.title or "").lower()
            or query_text in (item.device_label or "").lower()
            or query_text in (item.actor or "").lower()
            or query_text in (item.kind_title or "").lower()
        ]

    total = len(items)
    start = (page - 1) * per_page
    page_items = items[start : start + per_page]
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }
