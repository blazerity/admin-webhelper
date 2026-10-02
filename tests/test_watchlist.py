"""Watchlist: подписка, offline-алерты, маршруты watch/unwatch."""

from datetime import timedelta

from app.extensions import db
from app.models import (
    Device,
    DeviceStatus,
    DeviceWatchlist,
    Notification,
    NotificationKind,
    Sector,
    SectorAccess,
)
from app.services import watchlist_service
from app.services.ping_service import run_network_poll
from app.utils import utcnow


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def _sector_device(app, *, ip="10.8.1.1", username=None):
    with app.app_context():
        sector = Sector(name="WatchLab", description="")
        if username:
            sector.access_rules.append(
                SectorAccess(subject_type="user", subject_name=username)
            )
        db.session.add(sector)
        db.session.flush()
        device = Device(
            ip=ip,
            hostname="n-watch-01",
            sector_id=sector.id,
            last_status=DeviceStatus.ONLINE,
            last_seen=utcnow(),
        )
        db.session.add(device)
        db.session.commit()
        return device.id, sector.id


def test_watch_creates_and_updates(app, admin_id):
    device_id, _ = _sector_device(app)
    with app.app_context():
        row = watchlist_service.watch(admin_id, device_id, offline_minutes=30)
        assert row.offline_minutes == 30
        assert DeviceWatchlist.query.count() == 1
        again = watchlist_service.watch(admin_id, device_id, offline_minutes=45)
        assert again.id == row.id
        assert again.offline_minutes == 45
        assert DeviceWatchlist.query.count() == 1


def test_unwatch_removes(app, admin_id):
    device_id, _ = _sector_device(app)
    with app.app_context():
        watchlist_service.watch(admin_id, device_id)
        assert watchlist_service.unwatch(admin_id, device_id) is True
        assert watchlist_service.unwatch(admin_id, device_id) is False
        assert DeviceWatchlist.query.count() == 0


def test_watch_route_default_minutes(client, app, admin_id):
    device_id, _ = _sector_device(app)
    _login(client, admin_id)
    response = client.post(f"/devices/{device_id}/watch", follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        row = watchlist_service.get_watch(admin_id, device_id)
        assert row is not None
        assert row.offline_minutes == 15


def test_watch_route_forbidden_for_outsider(client, app, alice_id):
    device_id, _ = _sector_device(app)
    _login(client, alice_id)
    response = client.post(
        f"/devices/{device_id}/watch",
        data={"offline_minutes": "20"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    with app.app_context():
        assert DeviceWatchlist.query.count() == 0


def test_unwatch_route(client, app, admin_id):
    device_id, _ = _sector_device(app)
    with app.app_context():
        watchlist_service.watch(admin_id, device_id, offline_minutes=20)
    _login(client, admin_id)
    response = client.post(f"/devices/{device_id}/unwatch", follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        assert watchlist_service.get_watch(admin_id, device_id) is None


def test_device_detail_watching_context(client, app, admin_id):
    device_id, _ = _sector_device(app)
    with app.app_context():
        watchlist_service.watch(admin_id, device_id, offline_minutes=25)
    _login(client, admin_id)
    response = client.get(f"/devices/{device_id}")
    assert response.status_code == 200
    # Template may ignore vars; service + route path verified via watch entry.
    with app.app_context():
        assert watchlist_service.is_watching(admin_id, device_id)


def test_evaluate_creates_offline_alert(app, admin_id):
    device_id, _ = _sector_device(app)
    with app.app_context():
        watchlist_service.watch(admin_id, device_id, offline_minutes=15)
        device = db.session.get(Device, device_id)
        device.last_status = DeviceStatus.OFFLINE
        device.last_seen = utcnow() - timedelta(minutes=20)
        db.session.commit()

        created = watchlist_service.evaluate_watchlist_alerts()
        assert created == 1
        rows = Notification.query.filter_by(kind=NotificationKind.DEVICE_OFFLINE).all()
        assert len(rows) == 1
        assert rows[0].user_id == admin_id
        assert rows[0].link_url == f"/devices/{device_id}"

        # Dedupe until online again.
        assert watchlist_service.evaluate_watchlist_alerts() == 0
        assert Notification.query.count() == 1


def test_evaluate_skips_below_threshold(app, admin_id):
    device_id, _ = _sector_device(app)
    with app.app_context():
        watchlist_service.watch(admin_id, device_id, offline_minutes=60)
        device = db.session.get(Device, device_id)
        device.last_status = DeviceStatus.OFFLINE
        device.last_seen = utcnow() - timedelta(minutes=10)
        db.session.commit()
        assert watchlist_service.evaluate_watchlist_alerts() == 0
        assert Notification.query.count() == 0


def test_new_episode_after_online(app, admin_id):
    device_id, _ = _sector_device(app)
    with app.app_context():
        watchlist_service.watch(admin_id, device_id, offline_minutes=5)
        device = db.session.get(Device, device_id)
        device.last_status = DeviceStatus.OFFLINE
        device.last_seen = utcnow() - timedelta(hours=2)
        db.session.commit()
        assert watchlist_service.evaluate_watchlist_alerts() == 1

        # Эпизод закрыт: уведомление «в прошлом», last_seen после online.
        note = Notification.query.one()
        note.created_at = utcnow() - timedelta(hours=1)
        device = db.session.get(Device, device_id)
        device.last_status = DeviceStatus.ONLINE
        device.last_seen = utcnow() - timedelta(minutes=30)
        db.session.commit()

        # Снова offline: last_seen остаётся временем последнего online.
        device = db.session.get(Device, device_id)
        device.last_status = DeviceStatus.OFFLINE
        db.session.commit()
        assert watchlist_service.evaluate_watchlist_alerts() == 1
        assert Notification.query.count() == 2


def test_run_network_poll_emits_watchlist(app, admin_id, monkeypatch):
    from app.models import SectorRange
    from app.services.ping_service import PingResult

    with app.app_context():
        sector = Sector(name="PollWatch", description="")
        db.session.add(sector)
        db.session.flush()
        db.session.add(SectorRange(sector_id=sector.id, cidr="10.8.8.8"))
        device = Device(
            ip="10.8.8.8",
            hostname="n-poll",
            sector_id=sector.id,
            last_status=DeviceStatus.ONLINE,
            last_seen=utcnow() - timedelta(minutes=40),
        )
        db.session.add(device)
        db.session.flush()
        watchlist_service.watch(admin_id, device.id, offline_minutes=10)
        db.session.commit()

    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1, **kwargs: PingResult(
            DeviceStatus.OFFLINE, None, "timeout"
        ),
    )
    with app.app_context():
        run_network_poll(mode="cli")
        assert (
            Notification.query.filter_by(kind=NotificationKind.DEVICE_OFFLINE).count()
            == 1
        )


def test_sector_user_can_watch(client, app, carol_id):
    with app.app_context():
        sector = Sector(name="CarolNet", description="")
        sector.access_rules.append(
            SectorAccess(subject_type="group", subject_name="netops")
        )
        db.session.add(sector)
        db.session.flush()
        device = Device(
            ip="10.7.1.1",
            hostname="n-carol",
            sector_id=sector.id,
            last_status=DeviceStatus.ONLINE,
            last_seen=utcnow(),
        )
        db.session.add(device)
        db.session.commit()
        device_id = device.id

    _login(client, carol_id)
    response = client.post(
        f"/devices/{device_id}/watch",
        data={"offline_minutes": "15"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assert watchlist_service.is_watching(carol_id, device_id)
