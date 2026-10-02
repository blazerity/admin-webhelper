"""Поиск и карточка устройства: права, экранирование LIKE, маршруты."""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Device, DeviceHistory, Sector, SectorAccess, User
from app.services.search_service import search_devices, suggest_devices


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _sector(name, username):
    sector = Sector(name=name, description="")
    sector.access_rules.append(
        SectorAccess(subject_type="user", subject_name=username)
    )
    db.session.add(sector)
    db.session.flush()
    return sector


def _device(ip, sector, hostname="host", mac="AA:BB:CC:DD:EE:FF", status="unknown"):
    device = Device(
        ip=ip,
        hostname=hostname,
        mac=mac,
        sector_id=sector.id,
        last_status=status,
    )
    db.session.add(device)
    db.session.flush()
    return device


def test_alice_finds_only_her_device_by_ip_fragment(app, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        _device(ip="10.0.0.5", sector=own, hostname="printer", mac="AA:BB:CC:DD:EE:01")
        _device(ip="10.8.8.8", sector=other, hostname="printer", mac="AA:BB:CC:DD:EE:02")
        db.session.commit()
        alice = db.session.get(User, alice_id)

        assert [item.ip for item in search_devices(alice, "10.0.0")] == ["10.0.0.5"]
        assert [item.ip for item in search_devices(alice, "  0.0.5 ")] == ["10.0.0.5"]
        assert [item.ip for item in search_devices(alice, "ee:01")] == ["10.0.0.5"]
        assert search_devices(alice, "") == []
        assert search_devices(alice, "   ") == []
        assert suggest_devices(alice, "1") == []


def test_alice_does_not_see_foreign_hostname(app, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        mine = _device(ip="10.1.0.5", sector=own, hostname="Switch")
        _device(ip="10.2.0.5", sector=other, hostname="Switch")
        db.session.commit()
        alice = db.session.get(User, alice_id)

        found = search_devices(alice, "switch")
        assert [item.id for item in found] == [mine.id]
        assert search_devices(alice, "switch", sector_id=other.id) == []
        assert [item.id for item in search_devices(alice, "switch", sector_id=own.id)] == [
            mine.id
        ]


def test_percent_is_literal_not_wildcard(app, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        _device(ip="10.1.1.1", sector=own, hostname="plain")
        _device(ip="10.9.9.9", sector=own, hostname="other")
        literal = _device(ip="10.2.2.2", sector=own, hostname="srv-10%")
        db.session.commit()
        alice = db.session.get(User, alice_id)

        found = search_devices(alice, "10%")
        assert [item.id for item in found] == [literal.id]


def test_suggest_url_points_at_device(app, client, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        mine = _device(
            ip="10.0.0.5",
            sector=own,
            hostname="printer",
            mac="AA:BB:CC:DD:EE:01",
        )
        foreign = _device(ip="10.0.0.6", sector=other, hostname="printer")
        db.session.commit()
        mine_id = mine.id
        foreign_id = foreign.id

    _login(client, alice_id)
    response = client.get("/search/suggest", query_string={"q": "10.0"})
    assert response.status_code == 200
    items = response.get_json()
    assert len(items) == 1
    assert items[0]["label"] == "10.0.0.5  printer  AA:BB:CC:DD:EE:01"
    assert f"/devices/{mine_id}" in items[0]["url"]
    assert f"/devices/{foreign_id}" not in items[0]["url"]

    short = client.get("/search/suggest", query_string={"q": "1"})
    assert short.get_json() == []


def test_search_page_redirects_to_map(app, client, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        _device(ip="10.0.0.5", sector=own, hostname="printer")
        db.session.commit()
        own_id = own.id

    _login(client, alice_id)
    response = client.get("/search", query_string={"q": "printer", "sector_id": own_id})
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/")
    assert "q=printer" in response.headers["Location"]
    assert f"sector_id={own_id}" in response.headers["Location"]

    map_page = client.get(response.headers["Location"])
    assert map_page.status_code == 200
    text = map_page.get_data(as_text=True)
    assert "10.0.0.5" in text
    assert "Склад" in text
    assert "map-search" in text


def test_search_api_returns_accessible_devices(app, client, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        _device(ip="10.0.0.5", sector=own, hostname="printer")
        _device(ip="10.0.0.6", sector=other, hostname="printer")
        db.session.commit()

    _login(client, alice_id)
    response = client.get("/search/api", query_string={"q": "printer"})
    assert response.status_code == 200
    items = response.get_json()
    assert len(items) == 1
    assert items[0]["ip"] == "10.0.0.5"
    assert items[0]["sector"] == "Склад"


def test_map_shows_accessible_devices_ordered_by_ip(app, client, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        other = _sector("Офис", "bob")
        _device(ip="10.0.0.2", sector=own, hostname="later", mac="AA:BB:CC:DD:EE:02")
        _device(ip="10.0.0.10", sector=own, hostname="earlier", mac="AA:BB:CC:DD:EE:10")
        _device(ip="10.9.9.9", sector=other, hostname="hidden")
        db.session.commit()

    _login(client, alice_id)
    response = client.get("/")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Склад" in text
    assert "Офис" not in text
    assert "10.9.9.9" not in text
    assert text.find("10.0.0.10") < text.find("10.0.0.2")


def test_detail_foreign_device_is_403(app, client, alice_id):
    with app.app_context():
        other = _sector("Офис", "bob")
        foreign = _device(ip="10.8.8.8", sector=other)
        db.session.commit()
        foreign_id = foreign.id

    _login(client, alice_id)
    response = client.get(f"/devices/{foreign_id}")
    assert response.status_code == 403


def test_detail_own_device_shows_newest_history(app, client, alice_id):
    with app.app_context():
        own = _sector("Склад", "alice")
        device = _device(ip="10.4.4.4", sector=own, status="unknown")
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(21):
            if index == 0:
                response_ms = 4242
            elif index == 20:
                response_ms = 7777
            else:
                response_ms = 1
            db.session.add(
                DeviceHistory(
                    device_id=device.id,
                    timestamp=base - timedelta(minutes=index),
                    status="offline",
                    response_time_ms=response_ms,
                )
            )
        db.session.commit()
        device_id = device.id

    _login(client, alice_id)
    response = client.get(f"/devices/{device_id}")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "10.4.4.4" in text
    assert "Склад" in text
    assert "4242" in text
    assert "7777" not in text
    assert text.count("недоступен") == 20
