from app.extensions import db
from app.models import Device, Sector, SectorAccess
from app.utils import utcnow


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _seed_office(app, owner="admin"):
    with app.app_context():
        sector = Sector(name="Офис", description="")
        sector.access_rules.append(SectorAccess(subject_type="user", subject_name=owner))
        db.session.add(sector)
        db.session.flush()
        seen = utcnow()
        devices = [
            Device(
                ip="10.0.0.1",
                hostname="NBOOK01",
                sector_id=sector.id,
                last_status="online",
                last_seen=seen,
            ),
            Device(
                ip="10.0.0.2",
                hostname="WDESK01",
                sector_id=sector.id,
                last_status="offline",
                last_seen=seen,
            ),
            Device(
                ip="10.0.0.3",
                hostname="SRV01",
                sector_id=sector.id,
                last_status="online",
                last_seen=seen,
            ),
        ]
        db.session.add_all(devices)
        db.session.commit()
        return sector.id, [device.id for device in devices]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_login_page(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Имя пользователя" in response.get_data(as_text=True)


def test_map_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_map_shows_collapsed_sectors_with_active_count(client, app, admin_id):
    _seed_office(app)

    _login(client, admin_id)
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Офис" in html
    assert "2" in html and "активн" in html
    assert 'aria-expanded="true"' in html
    assert "collapse show" in html
    assert "Ноутбук" in html
    assert 'title="СБ"' in html
    assert "Тип неизвестен" in html
    assert "Карта сети" in html
    assert "Настройки" in html
    assert "Поиск устройств" not in html
    assert "Администрирование" not in html
    assert "data-filter-group=" in html
    assert "Ноутбуки" in html
    assert ">СБ<" in html
    assert "map.js" in html
    assert "map-search" in html
    assert "search.js" in html


def test_map_status_json(client, app, admin_id):
    sector_id, device_ids = _seed_office(app)
    _login(client, admin_id)

    response = client.get("/map/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert "updated_at" in payload
    assert len(payload["sectors"]) == 1
    sector = payload["sectors"][0]
    assert sector["id"] == sector_id
    assert sector["online"] == 2
    assert sector["total"] == 3
    by_id = {item["id"]: item for item in sector["devices"]}
    assert by_id[device_ids[0]]["kind"] == "notebook"
    assert by_id[device_ids[0]]["status"] == "online"
    assert by_id[device_ids[1]]["kind"] == "desktop"
    assert by_id[device_ids[2]]["kind"] == "other"


def test_device_kind_from_hostname(app):
    with app.app_context():
        assert Device(ip="10.0.0.1", hostname="nbook", sector_id=1).kind == "notebook"
        assert Device(ip="10.0.0.2", hostname="Wdesk", sector_id=1).kind == "desktop"
        assert Device(ip="10.0.0.3", hostname=None, sector_id=1).kind == "other"
