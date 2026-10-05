"""CSV-экспорт УЗ, действий и прогонов опроса."""

from __future__ import annotations

import csv
import io
from typing import Iterable

from flask import Response

from app.services.account_service import list_visible_accounts
from app.services.action_service import list_system_actions
from app.services.ping_service import load_recent_poll_runs
from app.services.hardware_poll_service import load_recent_hardware_poll_runs


def _writerows(headers: list[str], rows: Iterable[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def csv_attachment(body: str, filename: str) -> Response:
    """Общий Response для CSV-скачивания."""
    return Response(
        body,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def export_accounts_csv(user, *, q: str = "", limit: int = 5000) -> str:
    payload = list_visible_accounts(user, q=q, page=1, per_page=limit)
    rows = [
        [
            account.id,
            account.domain or "",
            account.username or "",
            account.display_name or "",
        ]
        for account in payload["items"]
    ]
    return _writerows(["id", "domain", "username", "display_name"], rows)


def export_actions_csv(user, *, kind: str = "", limit: int = 500) -> str:
    payload = list_system_actions(user, kind=kind, limit=limit)
    rows = [
        [
            item.kind_code,
            item.kind_title,
            item.title,
            item.status,
            item.when.isoformat() if item.when else "",
            item.device_id or "",
            item.device_label or "",
            item.actor or "",
            item.url or "",
        ]
        for item in payload["items"]
    ]
    return _writerows(
        [
            "kind_code",
            "kind_title",
            "title",
            "status",
            "when",
            "device_id",
            "device_label",
            "actor",
            "url",
        ],
        rows,
    )


def export_poll_runs_csv(*, limit: int = 100) -> str:
    runs = load_recent_poll_runs(limit=limit)
    rows = [
        [
            run.id,
            run.mode,
            run.started_at.isoformat() if run.started_at else "",
            run.finished_at.isoformat() if run.finished_at else "",
            run.scanned,
            run.online,
            run.offline,
            run.errors,
            (run.error or "").replace("\n", " ")[:500],
        ]
        for run in runs
    ]
    return _writerows(
        [
            "id",
            "mode",
            "started_at",
            "finished_at",
            "scanned",
            "online",
            "offline",
            "errors",
            "error",
        ],
        rows,
    )


def export_hardware_poll_runs_csv(*, limit: int = 100) -> str:
    runs = load_recent_hardware_poll_runs(limit=limit)
    rows = [
        [
            run.id,
            run.mode,
            run.started_at.isoformat() if run.started_at else "",
            run.finished_at.isoformat() if run.finished_at else "",
            run.scanned,
            run.online,
            run.offline,
            run.collected,
            run.changed,
            run.skipped,
            run.errors,
            (run.error or "").replace("\n", " ")[:500],
        ]
        for run in runs
    ]
    return _writerows(
        [
            "id",
            "mode",
            "started_at",
            "finished_at",
            "scanned",
            "online",
            "offline",
            "collected",
            "changed",
            "skipped",
            "errors",
            "error",
        ],
        rows,
    )
