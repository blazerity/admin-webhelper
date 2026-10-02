from app.extensions import db
from app.models import Device, Sector, SectorAccess


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


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
    with app.app_context():
        sector = Sector(name="Офис", description="")
        sector.access_rules.append(SectorAccess(subject_type="user", subject_name="admin"))
        db.session.add(sector)
        db.session.flush()
        db.session.add_all(
            [
                Device(ip="10.0.0.1", hostname="NBOOK01", sector_id=sector.id, last_status="online"),
                Device(ip="10.0.0.2", hostname="WDESK01", sector_id=sector.id, last_status="offline"),
                Device(ip="10.0.0.3", hostname="SRV01", sector_id=sector.id, last_status="online"),
            ]
        )
        db.session.commit()

    _login(client, admin_id)
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Офис" in html
    assert "2" in html and "активн" in html
    assert 'aria-expanded="false"' in html
    assert "Ноутбук" in html
    assert "Компьютер" in html
    assert "Тип неизвестен" in html
    assert "Карта сети" in html
    assert "Администрирование" in html
