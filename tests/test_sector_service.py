"""Секторы: сервис сохранения и несколько проверок маршрута.

Сервис зовём прямо в контексте приложения (фикстура app).
Для POST нужен Flask-Login: id кладётся в сессию как _user_id.
"""

import pytest

from app.extensions import db
from app.models import Device, DeviceHistory, Sector, SectorAccess, SectorRange
from app.services.net_utils import NetworkInputError
from app.services.sector_service import SectorError, delete_sector, save_sector


def _login_as(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_save_creates_sector_ranges_and_access(app):
    sector = save_sector(
        None,
        "  Склад  ",
        "описание",
        "10.0.0.5\n\n10.0.1.0/30\n",
        " alice , ",
        "netops",
    )
    assert sector.name == "Склад"
    assert [item.cidr for item in sector.ranges] == ["10.0.0.5", "10.0.1.0/30"]
    users = [rule.subject_name for rule in sector.access_rules if rule.subject_type == "user"]
    groups = [rule.subject_name for rule in sector.access_rules if rule.subject_type == "group"]
    assert users == ["alice"]
    assert groups == ["netops"]


def test_overlapping_name_raises(app):
    save_sector(None, "Склад", "", "10.0.0.1", "", "")
    with pytest.raises(SectorError):
        save_sector(None, "Склад", "", "10.0.0.2", "", "")


def test_wide_network_raises_and_does_not_persist(app):
    with pytest.raises(NetworkInputError):
        save_sector(None, "Широкий", "", "10.0.0.0/8", "", "")
    assert Sector.query.count() == 0


def test_update_replaces_ranges_and_keeps_devices(app):
    sector = save_sector(
        None,
        "Склад",
        "старое",
        "10.0.0.5\n10.0.1.0/30",
        "alice",
        "ops",
    )
    device = Device(ip="10.0.0.5", sector_id=sector.id, last_status="unknown")
    db.session.add(device)
    db.session.commit()
    device_id = device.id

    updated = save_sector(sector.id, "Склад", "новое", "192.168.1.10", "bob", "")
    assert updated.description == "новое"
    assert [item.cidr for item in updated.ranges] == ["192.168.1.10"]
    access = {(rule.subject_type, rule.subject_name) for rule in updated.access_rules}
    assert access == {("user", "bob")}
    kept = db.session.get(Device, device_id)
    assert kept is not None
    assert kept.sector_id == sector.id


def test_delete_removes_sector_and_missing_raises(app):
    sector = save_sector(None, "Склад", "", "10.0.0.5", "alice", "ops")
    device = Device(ip="10.0.0.5", sector_id=sector.id, last_status="unknown")
    db.session.add(device)
    db.session.flush()
    history = DeviceHistory(device_id=device.id, status="online")
    db.session.add(history)
    db.session.commit()
    sector_id = sector.id
    device_id = device.id
    history_id = history.id

    delete_sector(sector_id)

    assert db.session.get(Sector, sector_id) is None
    assert db.session.get(Device, device_id) is None
    assert db.session.get(DeviceHistory, history_id) is None
    assert SectorRange.query.filter_by(sector_id=sector_id).count() == 0
    assert SectorAccess.query.filter_by(sector_id=sector_id).count() == 0
    with pytest.raises(SectorError):
        delete_sector(sector_id)


def test_anonymous_post_redirects_to_login(client):
    response = client.post("/sectors/", data={"name": "Склад", "ranges": "10.0.0.1"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_non_admin_cannot_create(client, alice_id):
    with client:
        _login_as(client, alice_id)
        response = client.post(
            "/sectors/",
            data={
                "name": "Склад",
                "description": "",
                "ranges": "10.0.0.5",
                "access_users": "",
                "access_groups": "",
            },
        )
    assert response.status_code == 403


def test_admin_post_creates_row(client, admin_id):
    with client:
        _login_as(client, admin_id)
        response = client.post(
            "/sectors/",
            data={
                "name": "Склад",
                "description": "полки",
                "ranges": "10.0.0.5\n10.0.1.0/30",
                "access_users": "alice",
                "access_groups": "netops",
            },
        )
    assert response.status_code == 302
    assert "/sectors/" in response.headers["Location"]
    sector = Sector.query.filter_by(name="Склад").one()
    assert [item.cidr for item in sector.ranges] == ["10.0.0.5", "10.0.1.0/30"]
    assert {rule.subject_name for rule in sector.access_rules} == {"alice", "netops"}


def test_failed_update_keeps_saved_row(client, admin_id):
    sector = save_sector(None, "Склад", "описание", "10.0.0.5", "alice", "ops")
    with client:
        _login_as(client, admin_id)
        response = client.post(
            f"/sectors/{sector.id}",
            data={
                "name": "Склад",
                "description": "черновик",
                "ranges": "10.0.0.0/8",
                "access_users": "bob",
                "access_groups": "",
            },
        )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "10.0.0.0/8" in page
    assert "bob" in page
    saved = db.session.get(Sector, sector.id)
    assert saved.name == "Склад"
    assert saved.description == "описание"
    assert [item.cidr for item in saved.ranges] == ["10.0.0.5"]


def test_missing_sector_is_404_for_admin(client, admin_id):
    with client:
        _login_as(client, admin_id)
        edit = client.get("/sectors/99999/edit")
        update = client.post("/sectors/99999", data={"name": "Нет", "ranges": "10.0.0.1"})
        delete = client.post("/sectors/99999/delete")
    assert edit.status_code == 404
    assert update.status_code == 404
    assert delete.status_code == 404


def test_failed_create_redisplays_form_without_row(client, admin_id):
    with client:
        _login_as(client, admin_id)
        response = client.post(
            "/sectors/",
            data={
                "name": "Склад",
                "description": "черновик",
                "ranges": "10.0.0.0/8",
                "access_users": "alice",
                "access_groups": "netops",
            },
        )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "10.0.0.0/8" in page
    assert "черновик" in page
    assert "alice" in page
    assert "netops" in page
    assert Sector.query.count() == 0
