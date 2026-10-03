"""Матрица Authz v2: admin / operator / viewer / password_viewer / sector / none."""

import pytest

from app.authz import (
    accessible_devices,
    accessible_sectors,
    filter_accessible_devices,
    user_can_access_device,
    user_can_bulk_ping,
    user_can_bulk_script,
    user_can_run_diagnostics,
    user_can_run_script,
    user_can_run_scripts,
    user_can_view_password_expiry,
    user_has_role,
)
from app.extensions import db
from app.models import Device, Script, Sector, SectorAccess, User


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
    """Выставить сессию пользователя.

    conftest держит app_context на весь тест, а Flask-Login кэширует
    текущего юзера в ``g._login_user`` — сбрасываем при смене пользователя.
    """
    from flask import g, has_app_context

    with client.session_transaction() as sess:
        sess.clear()
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
    if has_app_context():
        g.pop("_login_user", None)


def _user(**flags) -> User:
    user = User(
        username=flags.pop("username"),
        display_name=flags.pop("display_name", flags.get("username", "u")),
        is_admin=flags.pop("is_admin", False),
        is_viewer=flags.pop("is_viewer", False),
        is_operator=flags.pop("is_operator", False),
        is_password_viewer=flags.pop("is_password_viewer", False),
    )
    db.session.add(user)
    db.session.commit()
    return user


def _script(name: str, *, published: bool = True) -> Script:
    script = Script(
        name=name,
        description="",
        target_os="windows",
        interpreter="powershell",
        storage="db",
        content="Write-Host hi",
        is_published=published,
    )
    db.session.add(script)
    db.session.commit()
    return script


@pytest.fixture
def operator_id(app):
    with app.app_context():
        return _user(username="oper", display_name="Оператор", is_operator=True).id


@pytest.fixture
def viewer_id(app):
    with app.app_context():
        return _user(username="viewer", display_name="Зритель", is_viewer=True).id


@pytest.fixture
def password_viewer_id(app):
    with app.app_context():
        return _user(
            username="pwdview",
            display_name="Пароли",
            is_password_viewer=True,
        ).id


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


def test_user_has_role_admin_implies_all(app, admin_id, alice_id, operator_id, viewer_id):
    with app.app_context():
        admin = db.session.get(User, admin_id)
        alice = db.session.get(User, alice_id)
        oper = db.session.get(User, operator_id)
        viewer = db.session.get(User, viewer_id)
        assert user_has_role(admin, "admin") is True
        assert user_has_role(admin, "operator") is True
        assert user_has_role(admin, "viewer") is True
        assert user_has_role(admin, "password_viewer") is True
        assert user_has_role(oper, "operator") is True
        assert user_has_role(oper, "admin") is False
        assert user_has_role(viewer, "viewer") is True
        assert user_has_role(viewer, "operator") is False
        assert user_has_role(alice, "viewer") is False
        assert user_has_role(None, "admin") is False


def test_user_can_run_scripts_admin_or_operator(app, admin_id, alice_id, operator_id, viewer_id):
    with app.app_context():
        admin = db.session.get(User, admin_id)
        alice = db.session.get(User, alice_id)
        oper = db.session.get(User, operator_id)
        viewer = db.session.get(User, viewer_id)
        assert user_can_run_scripts(admin) is True
        assert user_can_run_scripts(oper) is True
        assert user_can_run_scripts(alice) is False
        assert user_can_run_scripts(viewer) is False
        assert user_can_run_scripts(None) is False


def test_user_can_run_script_published_only_for_operator(app, admin_id, operator_id):
    with app.app_context():
        admin = db.session.get(User, admin_id)
        oper = db.session.get(User, operator_id)
        pub = _script("pub", published=True)
        draft = _script("draft", published=False)
        assert user_can_run_script(admin, pub) is True
        assert user_can_run_script(admin, draft) is True
        assert user_can_run_script(oper, pub) is True
        assert user_can_run_script(oper, draft) is False
        assert user_can_run_script(oper, None) is False


def test_user_can_view_password_expiry(app, admin_id, alice_id, password_viewer_id):
    with app.app_context():
        admin = db.session.get(User, admin_id)
        alice = db.session.get(User, alice_id)
        pwd = db.session.get(User, password_viewer_id)
        assert user_can_view_password_expiry(admin) is True
        assert user_can_view_password_expiry(pwd) is True
        assert user_can_view_password_expiry(alice) is False


def test_viewer_cannot_diagnostics_sector_user_can(app, alice_id, viewer_id, operator_id, admin_id):
    with app.app_context():
        alice = db.session.get(User, alice_id)
        viewer = db.session.get(User, viewer_id)
        oper = db.session.get(User, operator_id)
        admin = db.session.get(User, admin_id)
        assert user_can_run_diagnostics(alice) is True
        assert user_can_run_diagnostics(viewer) is False
        assert user_can_run_diagnostics(oper) is True
        assert user_can_run_diagnostics(admin) is True
        assert user_can_bulk_ping(viewer) is False
        assert user_can_bulk_ping(alice) is True


def test_user_can_bulk_script_wraps_run_scripts(app, admin_id, alice_id, operator_id):
    with app.app_context():
        admin = db.session.get(User, admin_id)
        alice = db.session.get(User, alice_id)
        oper = db.session.get(User, operator_id)
        assert user_can_bulk_script(admin) is True
        assert user_can_bulk_script(oper) is True
        assert user_can_bulk_script(alice) is False
        assert user_can_bulk_script(None) is False


def test_filter_accessible_devices_order_and_skip(app, admin_id, alice_id):
    """Order-preserving; missing and inaccessible ids are skipped (W2-06)."""
    with app.app_context():
        alice_sector = _sector("Склад", users=["alice"])
        other_sector = _sector("Офис", users=["bob"])
        d_alice_a = Device(ip="10.0.1.1", sector_id=alice_sector.id, last_status="unknown")
        d_other = Device(ip="10.0.1.2", sector_id=other_sector.id, last_status="unknown")
        d_alice_b = Device(ip="10.0.1.3", sector_id=alice_sector.id, last_status="unknown")
        db.session.add_all([d_alice_a, d_other, d_alice_b])
        db.session.commit()

        alice = db.session.get(User, alice_id)
        admin = db.session.get(User, admin_id)
        missing_id = max(d_alice_a.id, d_other.id, d_alice_b.id) + 999

        alice_result = filter_accessible_devices(
            alice,
            [d_alice_b.id, missing_id, d_other.id, d_alice_a.id],
        )
        assert [device.id for device in alice_result] == [d_alice_b.id, d_alice_a.id]

        admin_result = filter_accessible_devices(
            admin,
            [d_other.id, d_alice_a.id, missing_id, d_alice_b.id],
        )
        assert [device.id for device in admin_result] == [
            d_other.id,
            d_alice_a.id,
            d_alice_b.id,
        ]

        assert filter_accessible_devices(alice, []) == []
        assert filter_accessible_devices(None, [d_alice_a.id]) == []


def test_accessible_devices_honours_sector_acl(app, alice_id):
    with app.app_context():
        alice_sector = _sector("Склад", users=["alice"])
        other = _sector("Офис", users=["bob"])
        d1 = Device(ip="10.0.2.1", sector_id=alice_sector.id, last_status="unknown")
        d2 = Device(ip="10.0.2.2", sector_id=other.id, last_status="unknown")
        db.session.add_all([d1, d2])
        db.session.commit()
        alice = db.session.get(User, alice_id)
        ips = {device.ip for device in accessible_devices(alice)}
        assert ips == {"10.0.2.1"}


def test_sector_user_cannot_use_script_routes(client, app, alice_id):
    with app.app_context():
        sector = _sector("Склад", users=["alice"])
        device = Device(ip="10.0.0.10", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        script = _script("blocked-for-alice", published=True)
        db.session.commit()
        device_id = device.id
        script_id = script.id

    _login(client, alice_id)

    assert client.get("/scripts/").status_code == 403
    assert client.get("/scripts/new").status_code == 403
    assert client.post("/scripts/", data={"name": "x"}).status_code == 403
    assert (
        client.post(f"/scripts/{script_id}/run", data={"device_id": device_id}).status_code
        == 403
    )
    assert (
        client.post(f"/devices/{device_id}/command", data={"command": "whoami"}).status_code
        == 403
    )


def test_viewer_ping_forbidden(client, app, viewer_id):
    with app.app_context():
        sector = _sector("Склад", users=["viewer"])
        device = Device(ip="10.0.0.20", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        db.session.commit()
        device_id = device.id

    _login(client, viewer_id)
    assert client.post(f"/devices/{device_id}/ping").status_code == 403
    assert client.post(f"/devices/{device_id}/tracert").status_code == 403
    assert client.post("/api/map/bulk/ping", json={"device_ids": [device_id]}).status_code == 403


def test_operator_lists_published_and_cannot_crud(client, app, operator_id):
    with app.app_context():
        sector = _sector("Склад", users=["oper"])
        device = Device(ip="10.0.0.30", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        pub = _script("published-one", published=True)
        draft = _script("draft-one", published=False)
        db.session.commit()
        device_id = device.id
        pub_id = pub.id
        draft_id = draft.id

    _login(client, operator_id)
    listing = client.get("/scripts/")
    assert listing.status_code == 200
    body = listing.get_data(as_text=True)
    assert "published-one" in body
    assert "draft-one" not in body

    assert client.get("/scripts/new").status_code == 403
    assert client.post("/scripts/", data={"name": "x", "content": "1"}).status_code == 403
    assert client.post(f"/scripts/{pub_id}", data={"name": "x"}).status_code == 403
    assert client.post(f"/scripts/{pub_id}/delete").status_code == 403

    # Unpublished: 403 even for GET edit / run.
    assert client.get(f"/scripts/{draft_id}/edit").status_code == 403
    assert (
        client.post(
            f"/scripts/{draft_id}/run",
            data={"device_id": device_id},
        ).status_code
        == 403
    )

    # Published: can open run form.
    assert client.get(f"/scripts/{pub_id}/edit").status_code == 200


def test_operator_device_script_run_published_only(client, app, operator_id):
    with app.app_context():
        sector = _sector("Склад", users=["oper"])
        device = Device(ip="10.0.0.31", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        pub = _script("card-pub", published=True)
        draft = _script("card-draft", published=False)
        db.session.commit()
        device_id = device.id
        pub_id = pub.id
        draft_id = draft.id

    _login(client, operator_id)
    assert (
        client.post(
            f"/devices/{device_id}/scripts/run",
            data={"script_id": str(draft_id)},
        ).status_code
        == 403
    )
    # Published: route accepts authz (may redirect to run or flash on exec env).
    response = client.post(
        f"/devices/{device_id}/scripts/run",
        data={"script_id": str(pub_id)},
    )
    assert response.status_code in {200, 302}


def test_password_viewer_dashboard_not_settings(client, app, password_viewer_id, alice_id):
    _login(client, password_viewer_id)
    assert client.get("/password-expiry/").status_code == 200
    assert client.get("/password-expiry/settings").status_code == 403

    _login(client, alice_id)
    assert client.get("/password-expiry/").status_code == 403


def test_admin_audit_route(client, app, admin_id, alice_id):
    _login(client, admin_id)
    assert client.get("/admin/audit").status_code == 200
    _login(client, alice_id)
    assert client.get("/admin/audit").status_code == 403


def test_actions_accounts_pagination_query_params(client, app, admin_id):
    _login(client, admin_id)
    actions = client.get("/actions/?page=1&per_page=10&q=&kind=ping")
    assert actions.status_code == 200
    accounts = client.get("/accounts/?page=1&per_page=10&q=eve")
    assert accounts.status_code == 200


def test_role_matrix_summary(app, admin_id, operator_id, viewer_id, password_viewer_id, alice_id):
    """Сводка матрицы прав для W3-07."""
    with app.app_context():
        admin = db.session.get(User, admin_id)
        oper = db.session.get(User, operator_id)
        viewer = db.session.get(User, viewer_id)
        pwd = db.session.get(User, password_viewer_id)
        alice = db.session.get(User, alice_id)
        pub = _script("matrix-pub", published=True)
        draft = _script("matrix-draft", published=False)

        matrix = [
            # user, run_scripts, run_pub, run_draft, diagnostics, pwd_expiry
            (admin, True, True, True, True, True),
            (oper, True, True, False, True, False),
            (viewer, False, False, False, False, False),
            (pwd, False, False, False, True, True),
            (alice, False, False, False, True, False),
        ]
        for user, run_s, run_pub, run_draft, diag, pwd_ok in matrix:
            assert user_can_run_scripts(user) is run_s, user.username
            assert user_can_run_script(user, pub) is run_pub, user.username
            assert user_can_run_script(user, draft) is run_draft, user.username
            assert user_can_run_diagnostics(user) is diag, user.username
            assert user_can_view_password_expiry(user) is pwd_ok, user.username


def test_no_access_and_viewer_http_edges(client, app, viewer_id, password_viewer_id):
    """W3-07 edges: no_access empty ACL; viewer scripts 403; password_viewer role flag."""
    with app.app_context():
        nobody = _user(username="nobody", display_name="Без доступа")
        nobody_id = nobody.id
        sector = _sector("Склад", users=["viewer"])
        device = Device(ip="10.0.0.40", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        _script("edge-pub", published=True)
        db.session.commit()
        device_id = device.id

        assert accessible_sectors(nobody) == []
        assert accessible_devices(nobody) == []
        assert user_can_access_device(nobody, device) is False
        assert user_can_run_scripts(nobody) is False
        assert user_can_run_diagnostics(nobody) is True  # не viewer — но устройств нет
        assert user_has_role(
            db.session.get(User, password_viewer_id), "password_viewer"
        ) is True
        assert user_has_role(nobody, "viewer") is False

    _login(client, nobody_id)
    assert client.get("/").status_code == 200
    assert client.get(f"/devices/{device_id}").status_code == 403
    assert client.get("/scripts/").status_code == 403
    assert client.get("/password-expiry/").status_code == 403
    assert client.get("/admin/audit").status_code == 403

    _login(client, viewer_id)
    assert client.get("/scripts/").status_code == 403
    assert client.post(f"/devices/{device_id}/ping").status_code == 403
    assert client.post("/api/map/bulk/script", json={"device_ids": [device_id]}).status_code == 403
