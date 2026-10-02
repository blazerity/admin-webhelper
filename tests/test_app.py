from datetime import timedelta

from app.extensions import db
from app.models import Device, DeviceHistory, RunStatus, RunType, ScriptRun, Sector, SectorAccess
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
                serial_number="NB-SERIAL-01",
                mac="AA:BB:CC:DD:00:01",
                sector_id=sector.id,
                last_status="online",
                last_seen=seen,
            ),
            Device(
                ip="10.0.0.2",
                hostname="WDESK01",
                serial_number="WD-SERIAL-02",
                mac="AA:BB:CC:DD:00:02",
                sector_id=sector.id,
                last_status="offline",
                last_seen=seen,
            ),
            Device(
                ip="10.0.0.3",
                hostname="SRV01",
                serial_number="SRV-SERIAL-03",
                mac="AA:BB:CC:DD:00:03",
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
    html = response.get_data(as_text=True)
    assert "Имя пользователя" in html
    assert "Способ входа" in html
    assert 'value="ldap"' in html
    assert 'value="local"' in html


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
    assert "map-search-inner" in html
    assert "map-search-input" in html
    assert "<aside" not in html
    assert "search.js" in html
    assert "NB-SERIAL-01" in html
    assert "AA:BB:CC:DD:00:01" in html
    assert "device-card-meta" in html
    assert "на карте останутся подходящие машины" in html


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
        notebook = Device(ip="10.0.0.1", hostname="nbook", sector_id=1)
        desktop = Device(ip="10.0.0.2", hostname="Wdesk", sector_id=1)
        other = Device(ip="10.0.0.3", hostname=None, sector_id=1)
        assert notebook.kind == "notebook"
        assert notebook.kind_label == "Ноутбук"
        assert desktop.kind == "desktop"
        assert desktop.kind_label == "Системный блок"
        assert other.kind == "other"
        assert other.kind_label == "Устройство"


def test_device_detail_shows_object_card(client, app, admin_id):
    sector_id, device_ids = _seed_office(app)
    _login(client, admin_id)

    response = client.get(f"/devices/{device_ids[0]}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Карточка устройства" in html
    assert "device-object" in html
    assert "device-portrait" in html
    assert "device-settings-layout" in html
    assert "device-settings-sidebar" in html
    assert "Ноутбук" in html
    assert "NBOOK01" in html
    assert "NB-SERIAL-01" in html
    assert "Серийный номер" in html
    assert "AA:BB:CC:DD:00:01" in html
    assert ">MAC<" in html or ">MAC</" in html or "MAC" in html
    assert "10.0.0.1" in html
    assert "Обзор" in html
    assert "Командная строка и скрипты" in html
    assert "Результаты опросов" in html
    assert f"/devices/{device_ids[0]}?tab=overview" in html
    assert f"/devices/{device_ids[0]}?tab=commands" in html
    assert f"/devices/{device_ids[0]}?tab=polls" in html
    # Overview: object card only — no history tables or command form.
    assert "Недавние запуски" not in html
    assert "Выполнить команду" not in html
    assert "Показать все" not in html


def test_device_detail_commands_tab(client, app, admin_id):
    _sector_id, device_ids = _seed_office(app)
    device_id = device_ids[0]
    now = utcnow()
    with app.app_context():
        for index in range(7):
            db.session.add(
                ScriptRun(
                    device_id=device_id,
                    user_id=admin_id,
                    run_type=RunType.COMMAND if index % 2 == 0 else RunType.SCRIPT,
                    command_text=f"echo cmd-{index}",
                    status=RunStatus.SUCCESS,
                    started_at=now - timedelta(minutes=index),
                )
            )
        # Ping/tracert must not appear on the commands tab.
        db.session.add(
            ScriptRun(
                device_id=device_id,
                user_id=admin_id,
                run_type=RunType.PING,
                command_text="ping 10.0.0.1",
                status=RunStatus.SUCCESS,
                started_at=now + timedelta(minutes=1),
            )
        )
        db.session.commit()

    _login(client, admin_id)
    response = client.get(f"/devices/{device_id}?tab=commands")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Командная строка" in html
    assert "Выполнить команду" in html
    assert f'action="/devices/{device_id}/command"' in html or f"/devices/{device_id}/command" in html
    assert "Недавние запуски" in html
    assert "Показать все" in html
    assert "echo cmd-0" in html
    assert "echo cmd-4" in html
    assert "echo cmd-5" not in html
    assert "ping 10.0.0.1" not in html
    assert "device-object" not in html
    assert "btn-outline-primary" not in html  # Ping/Tracert buttons live on polls

    all_response = client.get(f"/devices/{device_id}?tab=commands&all=1")
    assert all_response.status_code == 200
    all_html = all_response.get_data(as_text=True)
    assert "echo cmd-5" in all_html
    assert "echo cmd-6" in all_html
    assert "Показать все" not in all_html
    assert "ping 10.0.0.1" not in all_html


def test_device_detail_polls_tab(client, app, admin_id):
    _sector_id, device_ids = _seed_office(app)
    device_id = device_ids[0]
    now = utcnow()
    with app.app_context():
        for index in range(7):
            db.session.add(
                DeviceHistory(
                    device_id=device_id,
                    status="online" if index % 2 == 0 else "offline",
                    response_time_ms=10 + index,
                    timestamp=now - timedelta(minutes=index),
                )
            )
        db.session.commit()

    _login(client, admin_id)
    response = client.get(f"/devices/{device_id}?tab=polls")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Результаты опросов" in html
    assert "Проверки с сервера" in html
    assert "Ping" in html
    assert "Трассировка" in html
    assert f"/devices/{device_id}/ping" in html
    assert f"/devices/{device_id}/tracert" in html
    assert "Показать все" in html
    assert ">10<" in html
    assert ">14<" in html
    assert ">15<" not in html
    assert "Выполнить команду" not in html
    assert "device-object" not in html

    all_response = client.get(f"/devices/{device_id}?tab=polls&all=1")
    assert all_response.status_code == 200
    all_html = all_response.get_data(as_text=True)
    assert ">15<" in all_html
    assert ">16<" in all_html
    assert "Показать все" not in all_html


def test_device_detail_unknown_tab_defaults_to_overview(client, app, admin_id):
    _sector_id, device_ids = _seed_office(app)
    _login(client, admin_id)
    response = client.get(f"/devices/{device_ids[0]}?tab=unknown")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "device-object" in html
    assert "Выполнить команду" not in html
