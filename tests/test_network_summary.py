"""Сводка сети: сервис и GET /api/network/summary."""

from datetime import timedelta

from app.extensions import db
from app.models import (
    Device,
    DeviceStatus,
    NetworkPollRun,
    RunStatus,
    RunType,
    ScriptRun,
    Sector,
    SectorAccess,
    User,
)
from app.services.network_summary_service import get_network_summary
from app.utils import utcnow


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _sector(name, username=None):
    sector = Sector(name=name, description="")
    if username:
        sector.access_rules.append(
            SectorAccess(subject_type="user", subject_name=username)
        )
    db.session.add(sector)
    db.session.flush()
    return sector


def _device(ip, sector, status=DeviceStatus.UNKNOWN):
    device = Device(
        ip=ip,
        hostname=f"host-{ip}",
        sector_id=sector.id,
        last_status=status,
        last_seen=utcnow(),
    )
    db.session.add(device)
    db.session.flush()
    return device


def test_summary_empty_sectors(app, alice_id):
    with app.app_context():
        alice = db.session.get(User, alice_id)
        summary = get_network_summary(alice)
        assert summary["devices_total"] == 0
        assert summary["devices_online"] == 0
        assert summary["devices_offline"] == 0
        assert summary["devices_unknown"] == 0
        assert summary["last_poll"] is None
        assert summary["failed_script_runs_24h"] == 0


def test_summary_counts_only_accessible_devices(app, alice_id, admin_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        _device("10.0.0.1", own, DeviceStatus.ONLINE)
        _device("10.0.0.2", own, DeviceStatus.OFFLINE)
        _device("10.0.0.3", own, DeviceStatus.UNKNOWN)
        _device("10.9.9.9", other, DeviceStatus.ONLINE)
        db.session.commit()

        alice = db.session.get(User, alice_id)
        admin = db.session.get(User, admin_id)
        alice_summary = get_network_summary(alice)
        assert alice_summary["devices_total"] == 3
        assert alice_summary["devices_online"] == 1
        assert alice_summary["devices_offline"] == 1
        assert alice_summary["devices_unknown"] == 1

        admin_summary = get_network_summary(admin)
        assert admin_summary["devices_total"] == 4
        assert admin_summary["devices_online"] == 2


def test_summary_includes_last_poll_and_failed_runs(app, alice_id, admin_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        mine = _device("10.0.0.1", own, DeviceStatus.ONLINE)
        foreign = _device("10.9.9.9", other, DeviceStatus.ONLINE)
        started = utcnow() - timedelta(minutes=5)
        finished = utcnow() - timedelta(minutes=4)
        run = NetworkPollRun(
            started_at=started,
            finished_at=finished,
            mode="scheduled",
            scanned=10,
            online=7,
            offline=3,
            errors=0,
            error="",
        )
        db.session.add(run)
        db.session.add(
            ScriptRun(
                device_id=mine.id,
                user_id=admin_id,
                run_type=RunType.COMMAND,
                command_text="whoami",
                status=RunStatus.FAILED,
                started_at=utcnow() - timedelta(hours=1),
                log_text="",
            )
        )
        db.session.add(
            ScriptRun(
                device_id=foreign.id,
                user_id=admin_id,
                run_type=RunType.COMMAND,
                command_text="whoami",
                status=RunStatus.FAILED,
                started_at=utcnow() - timedelta(hours=1),
                log_text="",
            )
        )
        db.session.add(
            ScriptRun(
                device_id=mine.id,
                user_id=admin_id,
                run_type=RunType.COMMAND,
                command_text="old",
                status=RunStatus.FAILED,
                started_at=utcnow() - timedelta(days=2),
                log_text="",
            )
        )
        db.session.commit()
        run_id = run.id

        alice = db.session.get(User, alice_id)
        summary = get_network_summary(alice)
        assert summary["last_poll"]["id"] == run_id
        assert summary["last_poll"]["mode"] == "scheduled"
        assert summary["last_poll"]["scanned"] == 10
        assert summary["last_poll"]["online"] == 7
        assert summary["last_poll"]["offline"] == 3
        assert summary["last_poll"]["errors"] == 0
        assert summary["last_poll"]["error"] == ""
        assert summary["last_poll"]["started_at"]
        assert summary["last_poll"]["finished_at"]
        # Чужой сектор alice не видит — только свой failed.
        assert summary["failed_script_runs_24h"] == 1

        admin = db.session.get(User, admin_id)
        assert get_network_summary(admin)["failed_script_runs_24h"] == 2


def test_network_summary_api(client, app, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        _device("10.0.0.1", own, DeviceStatus.ONLINE)
        db.session.commit()

    _login(client, alice_id)
    response = client.get("/api/network/summary")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["devices_total"] == 1
    assert payload["devices_online"] == 1
    assert payload["devices_offline"] == 0
    assert payload["devices_unknown"] == 0
    assert "last_poll" in payload
    assert "failed_script_runs_24h" in payload


def test_network_summary_requires_login(client):
    assert client.get("/api/network/summary").status_code in {302, 401}
