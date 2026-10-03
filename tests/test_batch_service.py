"""Агрегация статуса batch по ScriptRun + RunStatus.FINISHED."""

from app.extensions import db
from app.models import (
    Device,
    RunStatus,
    RunType,
    ScriptRun,
    Sector,
    SectorAccess,
    User,
)
from app.services.batch_service import batch_status_payload, list_visible_batch_runs
from app.utils import utcnow


def _sector(name, username=None):
    sector = Sector(name=name, description="")
    if username:
        sector.access_rules.append(
            SectorAccess(subject_type="user", subject_name=username)
        )
    db.session.add(sector)
    db.session.flush()
    return sector


def _device(ip, sector):
    device = Device(
        ip=ip,
        hostname=f"pc-{ip}",
        sector_id=sector.id,
        last_status="online",
        last_seen=utcnow(),
    )
    db.session.add(device)
    db.session.flush()
    return device


def _run(batch_id, device, user_id, status, run_type=RunType.PING):
    row = ScriptRun(
        device_id=device.id,
        user_id=user_id,
        batch_id=batch_id,
        run_type=run_type,
        command_text=f"ping {device.ip}",
        status=status,
        started_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_batch_status_counts_and_finished(app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        d1 = _device("10.0.0.1", sector)
        d2 = _device("10.0.0.2", sector)
        d3 = _device("10.0.0.3", sector)
        batch_id = "batch-aaa-111"
        _run(batch_id, d1, admin_id, RunStatus.SUCCESS)
        _run(batch_id, d2, admin_id, RunStatus.FAILED)
        _run(batch_id, d3, admin_id, RunStatus.PENDING)
        db.session.commit()

        admin = db.session.get(User, admin_id)
        payload = batch_status_payload(admin, batch_id)
        assert payload is not None
        assert payload["batch_id"] == batch_id
        assert payload["total"] == 3
        assert payload["success"] == 1
        assert payload["failed"] == 1
        assert payload["pending"] == 1
        assert payload["running"] == 0
        assert payload["cancelled"] == 0
        assert payload["finished"] is False
        assert payload["runs"][0]["hostname"] == "pc-10.0.0.1"
        assert payload["runs"][0]["url"] == f"/scripts/runs/{payload['runs'][0]['id']}"


def test_batch_status_finished_when_all_terminal(app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        d1 = _device("10.1.0.1", sector)
        d2 = _device("10.1.0.2", sector)
        batch_id = "batch-bbb-222"
        _run(batch_id, d1, admin_id, RunStatus.SUCCESS)
        _run(batch_id, d2, admin_id, RunStatus.CANCELLED)
        db.session.commit()

        admin = db.session.get(User, admin_id)
        payload = batch_status_payload(admin, batch_id)
        assert payload["finished"] is True
        assert payload["total"] == 2
        assert all(run["status"] in RunStatus.FINISHED for run in payload["runs"])


def test_batch_status_hides_inaccessible_runs(app, alice_id, admin_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        d_own = _device("10.2.0.1", own)
        d_other = _device("10.9.0.1", other)
        batch_id = "batch-ccc-333"
        # Автор — admin; alice видит только свой сектор через device access.
        _run(batch_id, d_own, admin_id, RunStatus.SUCCESS)
        _run(batch_id, d_other, admin_id, RunStatus.RUNNING)
        db.session.commit()

        alice = db.session.get(User, alice_id)
        visible = list_visible_batch_runs(alice, batch_id)
        assert len(visible) == 1
        assert visible[0].device_id == d_own.id

        payload = batch_status_payload(alice, batch_id)
        assert payload["total"] == 1
        assert payload["success"] == 1
        assert payload["running"] == 0


def test_batch_status_none_when_no_visible(app, alice_id, admin_id):
    with app.app_context():
        other = _sector("Чужой", "bob")
        device = _device("10.8.0.1", other)
        batch_id = "batch-ddd-444"
        _run(batch_id, device, admin_id, RunStatus.PENDING)
        db.session.commit()

        alice = db.session.get(User, alice_id)
        assert batch_status_payload(alice, batch_id) is None
        assert batch_status_payload(alice, "missing") is None
