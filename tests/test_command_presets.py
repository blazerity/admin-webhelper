"""Пресеты команд и быстрый запуск скрипта с карточки устройства."""

from unittest.mock import patch

from flask import g, has_app_context

from app.extensions import db
from app.models import Device, Script, Sector, SectorAccess
from app.services.command_presets import COMMAND_PRESETS, list_command_presets
from app.services.script_service import ScriptError
from app.utils import utcnow


def _login(client, user_id):
    # App-фикстура держит app_context: Flask-Login кэширует user в g.
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


def test_list_command_presets_matches_contract():
    presets = list_command_presets()
    assert len(presets) == 4
    assert presets[0] == {"id": "whoami", "label": "whoami", "command": "whoami"}
    ids = [item["id"] for item in presets]
    assert ids == ["whoami", "ipconfig", "hostname", "netstat"]
    # Мутация копии не портит константу.
    presets[0]["command"] = "hacked"
    assert COMMAND_PRESETS[0]["command"] == "whoami"


def test_command_presets_api_forbidden_for_non_admin(client, alice_id):
    _login(client, alice_id)
    assert client.get("/api/command-presets").status_code == 403


def test_command_presets_api_admin_ok(client, admin_id):
    _login(client, admin_id)
    response = client.get("/api/command-presets")
    assert response.status_code == 200
    payload = response.get_json()
    assert "presets" in payload
    assert len(payload["presets"]) == 4
    assert payload["presets"][1]["command"] == "ipconfig /all"


def test_detail_context_has_presets_and_scripts_for_admin(client, app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        device = _device("10.1.1.1", sector)
        script = Script(
            name="cleanup",
            description="",
            target_os="windows",
            interpreter="powershell",
            storage="db",
            content="Get-Date",
            created_by_id=admin_id,
        )
        db.session.add(script)
        db.session.commit()
        device_id = device.id

    _login(client, admin_id)
    page = client.get(f"/devices/{device_id}?tab=commands")
    assert page.status_code == 200


def test_run_script_from_device_admin_ok(client, app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        device = _device("10.1.1.2", sector)
        script = Script(
            name="whoami_script",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="whoami",
            created_by_id=admin_id,
        )
        db.session.add(script)
        db.session.commit()
        device_id = device.id
        script_id = script.id

    class _FakeRun:
        id = 4242

    captured = {}

    def _fake_start(script, user, devices):
        captured["script_id"] = script.id
        captured["user_id"] = user.id
        captured["device_ids"] = [d.id for d in devices]
        return [_FakeRun()]

    _login(client, admin_id)
    with patch(
        "app.routes.devices.script_service.start_script_on_devices",
        side_effect=_fake_start,
    ):
        response = client.post(
            f"/devices/{device_id}/scripts/run",
            data={"script_id": str(script_id)},
        )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/scripts/runs/4242")
    assert captured["script_id"] == script_id
    assert captured["user_id"] == admin_id
    assert captured["device_ids"] == [device_id]


def test_run_script_from_device_requires_admin(client, app, alice_id):
    with app.app_context():
        sector = _sector("Склад", "alice")
        device = _device("10.2.2.2", sector)
        script = Script(
            name="noop",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="echo",
        )
        db.session.add(script)
        db.session.commit()
        device_id = device.id
        script_id = script.id

    _login(client, alice_id)
    response = client.post(
        f"/devices/{device_id}/scripts/run",
        data={"script_id": str(script_id)},
    )
    assert response.status_code == 403


def test_run_script_from_device_admin_visible_device(client, app, admin_id):
    """Admin + visible device → redirect на run detail."""
    with app.app_context():
        sector = _sector("Офис", "bob")
        device = _device("10.8.8.8", sector)
        script = Script(
            name="foreign_run",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="whoami",
        )
        db.session.add(script)
        db.session.commit()
        device_id = device.id
        script_id = script.id

    class _FakeRun:
        id = 7

    _login(client, admin_id)
    with patch(
        "app.routes.devices.script_service.start_script_on_devices",
        return_value=[_FakeRun()],
    ):
        response = client.post(
            f"/devices/{device_id}/scripts/run",
            data={"script_id": str(script_id)},
        )
    assert response.status_code == 302
    assert "/scripts/runs/7" in response.headers["Location"]


def test_run_script_script_error_redirects(client, app, admin_id):
    with app.app_context():
        sector = _sector("Lab")
        device = _device("10.3.3.3", sector)
        script = Script(
            name="bad",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="x",
            created_by_id=admin_id,
        )
        db.session.add(script)
        db.session.commit()
        device_id = device.id
        script_id = script.id

    _login(client, admin_id)
    with patch(
        "app.routes.devices.script_service.start_script_on_devices",
        side_effect=ScriptError("нет тела"),
    ):
        response = client.post(
            f"/devices/{device_id}/scripts/run",
            data={"script_id": str(script_id)},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert f"/devices/{device_id}" in response.headers["Location"]
