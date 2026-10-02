"""W2 edge smoke: map template markers, batch page login/404.

Имена намеренно вне test_bulk / test_batch / test_authz (владение A3/A4).
"""

from flask import g, has_app_context

from app.extensions import db
from app.models import Device, Sector, SectorAccess
from app.utils import utcnow


def _login(client, user_id):
    if has_app_context():
        g.pop("_login_user", None)
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _sector_with_device(name="RegLab", ip="10.9.9.1"):
    sector = Sector(name=name, description="")
    db.session.add(sector)
    db.session.flush()
    device = Device(
        ip=ip,
        hostname=f"pc-{ip}",
        sector_id=sector.id,
        last_status="online",
        last_seen=utcnow(),
    )
    db.session.add(device)
    db.session.flush()
    return sector, device


def test_map_template_smoke_device_cards_and_filters(client, app, admin_id):
    with app.app_context():
        _sector_with_device()
        db.session.commit()

    _login(client, admin_id)
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-device-card' in html
    assert 'id="map-filters"' in html
    assert 'data-filter-group="status"' in html
    assert 'data-filter-group="type"' in html
    assert 'id="sector-map"' in html
    assert "js/map.js" in html
    assert 'data-status-url="' in html


def test_map_template_smoke_empty_sectors_no_map_js(client, app, alice_id):
    """Non-admin без ACL: пустое состояние, скрипт карты не подключается."""
    _login(client, alice_id)
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Нет секторов" in html
    assert "js/map.js" not in html


def test_batch_page_requires_login(client):
    response = client.get("/scripts/batches/any-batch-id")
    assert response.status_code in {302, 401}
    if response.status_code == 302:
        assert "/login" in (response.headers.get("Location") or "")


def test_batch_page_unknown_returns_404(client, app, admin_id):
    _login(client, admin_id)
    response = client.get("/scripts/batches/no-such-batch-w2")
    assert response.status_code == 404


def test_batch_page_inaccessible_returns_404(client, app, alice_id, admin_id):
    """Пачка только с runs чужого сектора → SSR 404 для non-admin."""
    from app.models import RunStatus, RunType, ScriptRun

    with app.app_context():
        foreign = Sector(name="Чужой", description="")
        foreign.access_rules.append(
            SectorAccess(subject_type="user", subject_name="bob")
        )
        db.session.add(foreign)
        db.session.flush()
        device = Device(
            ip="10.9.9.8",
            hostname="pc-foreign",
            sector_id=foreign.id,
            last_status="online",
            last_seen=utcnow(),
        )
        db.session.add(device)
        db.session.flush()
        batch_id = "batch-hidden-from-alice"
        db.session.add(
            ScriptRun(
                device_id=device.id,
                user_id=admin_id,
                batch_id=batch_id,
                run_type=RunType.PING,
                command_text=f"ping {device.ip}",
                status=RunStatus.SUCCESS,
                started_at=utcnow(),
            )
        )
        db.session.commit()

    _login(client, alice_id)
    page = client.get(f"/scripts/batches/{batch_id}")
    assert page.status_code == 404
    api = client.get(f"/api/batches/{batch_id}")
    assert api.status_code == 404
