"""Bulk ping/script API и GET batch status / SSR page."""

from unittest.mock import patch

from flask import g, has_app_context

from app.extensions import db
from app.models import (
    Device,
    RunStatus,
    RunType,
    Script,
    ScriptRun,
    Sector,
    SectorAccess,
)
from app.utils import utcnow


def _login(client, user_id):
    if has_app_context():
        g.pop("_login_user", None)
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


def test_bulk_ping_requires_login(client):
    response = client.post(
        "/api/map/bulk/ping",
        json={"device_ids": [1]},
    )
    assert response.status_code in {302, 401}


def test_bulk_ping_admin_ok(client, app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        d1 = _device("10.0.1.1", sector)
        d2 = _device("10.0.1.2", sector)
        db.session.commit()
        ids = [d1.id, d2.id]

    class _FakeRun:
        def __init__(self, run_id):
            self.id = run_id

    captured = {}

    def _fake_start(user, devices):
        captured["user_id"] = user.id
        captured["device_ids"] = [d.id for d in devices]
        return "batch-ping-1", [_FakeRun(11), _FakeRun(12)]

    _login(client, admin_id)
    with patch(
        "app.routes.devices.batch_service.start_bulk_ping",
        side_effect=_fake_start,
    ):
        response = client.post(
            "/api/map/bulk/ping",
            json={"device_ids": ids},
        )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["batch_id"] == "batch-ping-1"
    assert payload["run_ids"] == [11, 12]
    assert payload["accepted"] == 2
    assert payload["skipped"] == 0
    assert payload["progress_url"] == "/scripts/batches/batch-ping-1"
    assert captured["user_id"] == admin_id
    assert captured["device_ids"] == ids


def test_bulk_ping_skips_inaccessible_and_403_if_none(client, app, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        d_own = _device("10.0.2.1", own)
        d_other = _device("10.0.2.9", other)
        db.session.commit()
        own_id = d_own.id
        other_id = d_other.id

    class _FakeRun:
        id = 99

    _login(client, alice_id)
    with patch(
        "app.routes.devices.batch_service.start_bulk_ping",
        return_value=("batch-mix", [_FakeRun()]),
    ) as mocked:
        ok = client.post(
            "/api/map/bulk/ping",
            json={"device_ids": [own_id, other_id]},
        )
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["accepted"] == 1
    assert body["skipped"] == 1
    mocked.assert_called_once()
    assert [d.id for d in mocked.call_args.args[1]] == [own_id]

    denied = client.post(
        "/api/map/bulk/ping",
        json={"device_ids": [other_id]},
    )
    assert denied.status_code == 403


def test_bulk_ping_rejects_over_100(client, app, admin_id):
    _login(client, admin_id)
    response = client.post(
        "/api/map/bulk/ping",
        json={"device_ids": list(range(1, 102))},
    )
    assert response.status_code == 400
    assert "100" in response.get_json()["error"]


def test_bulk_ping_accepts_form_urlencoded(client, app, admin_id):
    with app.app_context():
        sector = _sector("FormLab")
        device = _device("10.0.3.1", sector)
        db.session.commit()
        device_id = device.id

    class _FakeRun:
        id = 7

    _login(client, admin_id)
    with patch(
        "app.routes.devices.batch_service.start_bulk_ping",
        return_value=("batch-form", [_FakeRun()]),
    ):
        response = client.post(
            "/api/map/bulk/ping",
            data={"device_ids": str(device_id)},
            content_type="application/x-www-form-urlencoded",
        )
    assert response.status_code == 200
    assert response.get_json()["accepted"] == 1


def test_bulk_script_requires_admin(client, app, alice_id, admin_id):
    with app.app_context():
        sector = _sector("Склад", "alice")
        device = _device("10.0.4.1", sector)
        script = Script(
            name="noop",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="echo",
            created_by_id=admin_id,
        )
        db.session.add(script)
        db.session.commit()
        device_id = device.id
        script_id = script.id

    _login(client, alice_id)
    response = client.post(
        "/api/map/bulk/script",
        json={"device_ids": [device_id], "script_id": script_id},
    )
    assert response.status_code == 403


def test_bulk_script_admin_ok(client, app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        d1 = _device("10.0.5.1", sector)
        d2 = _device("10.0.5.2", sector)
        script = Script(
            name="whoami_bulk",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="whoami",
            created_by_id=admin_id,
        )
        db.session.add(script)
        db.session.commit()
        ids = [d1.id, d2.id]
        script_id = script.id

    class _FakeRun:
        def __init__(self, run_id):
            self.id = run_id

    captured = {}

    def _fake_start(script, user, devices):
        captured["script_id"] = script.id
        captured["user_id"] = user.id
        captured["device_ids"] = [d.id for d in devices]
        return "batch-script-1", [_FakeRun(21), _FakeRun(22)]

    _login(client, admin_id)
    with patch(
        "app.routes.devices.batch_service.start_bulk_script",
        side_effect=_fake_start,
    ):
        response = client.post(
            "/api/map/bulk/script",
            json={"device_ids": ids, "script_id": script_id},
        )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["batch_id"] == "batch-script-1"
    assert payload["run_ids"] == [21, 22]
    assert payload["progress_url"] == "/scripts/batches/batch-script-1"
    assert captured["script_id"] == script_id
    assert captured["device_ids"] == ids


def test_batch_status_api_and_ssr(client, app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        d1 = _device("10.0.6.1", sector)
        d2 = _device("10.0.6.2", sector)
        batch_id = "batch-ssr-1"
        for device, status in (
            (d1, RunStatus.SUCCESS),
            (d2, RunStatus.RUNNING),
        ):
            db.session.add(
                ScriptRun(
                    device_id=device.id,
                    user_id=admin_id,
                    batch_id=batch_id,
                    run_type=RunType.PING,
                    command_text=f"ping {device.ip}",
                    status=status,
                    started_at=utcnow(),
                )
            )
        db.session.commit()

    _login(client, admin_id)
    api = client.get(f"/api/batches/{batch_id}")
    assert api.status_code == 200
    payload = api.get_json()
    assert payload["total"] == 2
    assert payload["success"] == 1
    assert payload["running"] == 1
    assert payload["finished"] is False

    page = client.get(f"/scripts/batches/{batch_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert batch_id in html
    assert "batch-progress" in html

    missing = client.get("/api/batches/no-such-batch")
    assert missing.status_code == 404


def test_map_context_includes_map_scripts_for_admin(client, app, admin_id, alice_id):
    from flask import template_rendered

    with app.app_context():
        script = Script(
            name="map_script_a",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="echo",
            created_by_id=admin_id,
        )
        db.session.add(script)
        db.session.commit()
        script_id = script.id

    recorded = []

    def _record(sender, template, context, **extra):
        recorded.append(dict(context))

    _login(client, admin_id)
    with template_rendered.connected_to(_record, app):
        assert client.get("/").status_code == 200
    assert recorded
    assert recorded[-1]["map_scripts"] == [{"id": script_id, "name": "map_script_a"}]

    recorded.clear()
    _login(client, alice_id)
    with template_rendered.connected_to(_record, app):
        assert client.get("/").status_code == 200
    assert recorded[-1]["map_scripts"] == []


def test_map_status_includes_device_url(client, app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        device = _device("10.0.7.1", sector)
        db.session.commit()
        device_id = device.id

    _login(client, admin_id)
    response = client.get("/map/status")
    assert response.status_code == 200
    payload = response.get_json()
    devices = payload["sectors"][0]["devices"]
    by_id = {item["id"]: item for item in devices}
    assert by_id[device_id]["url"] == f"/devices/{device_id}"
