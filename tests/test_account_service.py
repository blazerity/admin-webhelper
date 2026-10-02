"""Справочник УЗ и запись появлений на устройствах."""

from datetime import timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import (
    Device,
    DeviceAccountHistory,
    EndpointAccount,
    Sector,
    SectorAccess,
    SectorRange,
)
from app.services.account_service import (
    apply_logged_on_user,
    get_or_create_account,
    list_visible_accounts,
    parse_windows_account,
)
from app.services.discovery_service import WmiInventory
from app.services.ping_service import PingResult, poll_all_sectors
from app.utils import utcnow


def test_parse_windows_account_variants():
    assert parse_windows_account(r"CORP\Alice") == ("CORP", "alice")
    assert parse_windows_account("alice@corp.local") == ("CORP.LOCAL", "alice")
    assert parse_windows_account("bob") == ("", "bob")
    assert parse_windows_account(".\\localuser") == ("", "localuser")
    assert parse_windows_account("") is None
    assert parse_windows_account(None) is None
    assert parse_windows_account("   ") is None


def test_get_or_create_account_is_idempotent(app):
    with app.app_context():
        first = get_or_create_account(username="Alice", domain="corp")
        db.session.commit()
        second = get_or_create_account(username="alice", domain="CORP")
        db.session.commit()
        assert first.id == second.id
        assert second.username == "alice"
        assert second.domain == "CORP"
        assert db.session.query(EndpointAccount).count() == 1


def test_apply_logged_on_user_writes_history_and_current(app):
    with app.app_context():
        sector = Sector(name="A", description="")
        db.session.add(sector)
        db.session.flush()
        device = Device(ip="10.0.0.5", sector_id=sector.id, last_status="unknown")
        db.session.add(device)
        db.session.flush()

        account = apply_logged_on_user(device, r"CORP\alice")
        db.session.commit()

        assert account is not None
        assert device.current_account_id == account.id
        assert device.current_account_seen_at is not None
        history = db.session.scalars(select(DeviceAccountHistory)).all()
        assert len(history) == 1
        assert history[0].raw_value == r"CORP\alice"

        # Тот же опрос сразу — без новой строки истории.
        apply_logged_on_user(device, r"CORP\alice")
        db.session.commit()
        assert db.session.query(DeviceAccountHistory).count() == 1

        # Смена УЗ — новая строка.
        other = apply_logged_on_user(device, r"CORP\bob")
        db.session.commit()
        assert other.username == "bob"
        assert device.current_account_id == other.id
        assert db.session.query(DeviceAccountHistory).count() == 2

        # Пусто — сброс текущей.
        assert apply_logged_on_user(device, None) is None
        db.session.commit()
        assert device.current_account_id is None


def test_apply_logged_on_user_dedup_expires(app):
    with app.app_context():
        sector = Sector(name="B", description="")
        db.session.add(sector)
        db.session.flush()
        device = Device(ip="10.0.0.6", sector_id=sector.id, last_status="online")
        db.session.add(device)
        db.session.flush()

        apply_logged_on_user(device, "alice")
        db.session.commit()
        old = db.session.scalar(select(DeviceAccountHistory))
        old.seen_at = utcnow() - timedelta(hours=2)
        db.session.commit()

        apply_logged_on_user(device, "alice")
        db.session.commit()
        assert db.session.query(DeviceAccountHistory).count() == 2


def test_poll_persists_logged_on_user(app, monkeypatch):
    with app.app_context():
        sector = Sector(name="PollAcc", description="")
        sector.ranges.append(SectorRange(cidr="10.9.0.5"))
        db.session.add(sector)
        db.session.commit()

        monkeypatch.setattr(
            "app.services.ping_service.ping_host",
            lambda ip, timeout_s=1: PingResult("online", 8, "ok"),
        )
        monkeypatch.setattr(
            "app.services.discovery_service.lookup_hostname",
            lambda ip: "n-acc",
        )
        monkeypatch.setattr(
            "app.services.discovery_service.lookup_mac",
            lambda ip: None,
        )
        monkeypatch.setattr(
            "app.services.discovery_service.lookup_wmi_inventory",
            lambda ip: WmiInventory(
                serial_number="ACCTAG1",
                mac="AA:BB:CC:DD:00:11",
                logged_on_user=r"CORP\carol",
            ),
        )

        poll_all_sectors()
        device = db.session.scalar(select(Device).where(Device.serial_number == "ACCTAG1"))
        assert device is not None
        assert device.current_account is not None
        assert device.current_account.account_key == r"CORP\carol"
        assert db.session.query(DeviceAccountHistory).count() == 1


def test_list_visible_accounts_respects_sector_access(app, alice_id, carol_id):
    with app.app_context():
        sector = Sector(name="Visible", description="")
        sector.access_rules.append(SectorAccess(subject_type="user", subject_name="carol"))
        db.session.add(sector)
        db.session.flush()
        device = Device(ip="10.0.0.7", sector_id=sector.id, last_status="online")
        db.session.add(device)
        db.session.flush()
        apply_logged_on_user(device, r"CORP\dave")
        db.session.commit()

        from app.models import User

        alice = db.session.get(User, alice_id)
        carol = db.session.get(User, carol_id)
        assert list_visible_accounts(alice) == []
        visible = list_visible_accounts(carol)
        assert len(visible) == 1
        assert visible[0].username == "dave"


def test_accounts_routes(client, app, admin_id):
    with app.app_context():
        sector = Sector(name="R", description="")
        db.session.add(sector)
        db.session.flush()
        device = Device(
            ip="10.0.0.8",
            hostname="nbook",
            sector_id=sector.id,
            last_status="online",
            last_seen=utcnow(),
        )
        db.session.add(device)
        db.session.flush()
        account = apply_logged_on_user(device, r"CORP\eve")
        db.session.commit()
        account_id = account.id
        device_id = device.id

    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)

    listing = client.get("/accounts/")
    assert listing.status_code == 200
    assert b"CORP\\eve" in listing.data or b"CORP\\eve".decode() in listing.get_data(as_text=True)

    detail = client.get(f"/accounts/{account_id}")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "CORP\\eve" in html
    assert "nbook" in html

    device_tab = client.get(f"/devices/{device_id}?tab=accounts")
    assert device_tab.status_code == 200
    assert "CORP\\eve" in device_tab.get_data(as_text=True)


def test_actions_route_shows_kinds(client, app, admin_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
    response = client.get("/actions/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "account_sighting" in html
    assert "Обнаружение УЗ" in html
