from app.authz import accessible_sectors, user_can_access_device, user_can_run_scripts
from app.extensions import db
from app.models import Device, Sector, SectorAccess, User


def _sector(name, users=(), groups=()):
    sector = Sector(name=name, description="")
    for username in users:
        sector.access_rules.append(SectorAccess(subject_type="user", subject_name=username))
    for group in groups:
        sector.access_rules.append(SectorAccess(subject_type="group", subject_name=group))
    db.session.add(sector)
    db.session.commit()
    return sector


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)


def test_admin_sees_every_sector(app, admin_id):
    with app.app_context():
        _sector("Склад")
        admin = db.session.get(User, admin_id)
        names = {sector.name for sector in accessible_sectors(admin)}
        assert names == {"Склад"}


def test_user_sees_only_named_sector(app, alice_id):
    with app.app_context():
        _sector("Склад", users=["alice"])
        _sector("Офис", users=["bob"])
        alice = db.session.get(User, alice_id)
        names = {sector.name for sector in accessible_sectors(alice)}
        assert names == {"Склад"}


def test_group_membership_grants_sector(app, carol_id):
    with app.app_context():
        sector = _sector("Цех", groups=["NetOps"])
        carol = db.session.get(User, carol_id)
        assert {item.name for item in accessible_sectors(carol)} == {"Цех"}
        device = Device(ip="10.0.0.8", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        db.session.commit()
        assert user_can_access_device(carol, device) is True


def test_stranger_does_not_see_device(app, alice_id, carol_id):
    with app.app_context():
        sector = _sector("Цех", groups=["netops"])
        device = Device(ip="10.0.0.9", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        db.session.commit()
        alice = db.session.get(User, alice_id)
        assert user_can_access_device(alice, device) is False


def test_user_can_run_scripts_admin_only(app, admin_id, alice_id):
    """W1 shim: scripts remain admin-only until Authz v2 operator lands in W3."""
    with app.app_context():
        admin = db.session.get(User, admin_id)
        alice = db.session.get(User, alice_id)
        assert user_can_run_scripts(admin) is True
        assert user_can_run_scripts(alice) is False
        assert user_can_run_scripts(None) is False


def test_non_admin_cannot_use_existing_script_routes(client, app, alice_id):
    """Прямой POST/GET к библиотеке скриптов и remote command — 403 без admin."""
    with app.app_context():
        sector = _sector("Склад", users=["alice"])
        device = Device(ip="10.0.0.10", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        db.session.commit()
        device_id = device.id

    _login(client, alice_id)

    assert client.get("/scripts/").status_code == 403
    assert client.get("/scripts/new").status_code == 403
    assert client.post("/scripts/", data={"name": "x"}).status_code == 403
    assert client.post("/scripts/1/run", data={"device_id": device_id}).status_code == 403
    assert (
        client.post(f"/devices/{device_id}/command", data={"command": "whoami"}).status_code
        == 403
    )


def test_device_script_run_route_forbids_non_admin_when_present(client, app, alice_id):
    """W1-07: POST /devices/<id>/scripts/run must be 403 for non-admin once A3 lands it."""
    import pytest

    with app.app_context():
        sector = _sector("Склад", users=["alice"])
        device = Device(ip="10.0.0.11", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        db.session.commit()
        device_id = device.id

    rule = f"/devices/<int:device_id>/scripts/run"
    rules = {item.rule for item in app.url_map.iter_rules()}
    if rule not in rules:
        pytest.skip(
            "A3 has not registered POST /devices/<id>/scripts/run yet; "
            "expected status 403 for non-admin when the route exists (ADR 002)."
        )

    _login(client, alice_id)
    response = client.post(
        f"/devices/{device_id}/scripts/run",
        data={"script_id": "1"},
    )
    assert response.status_code == 403
