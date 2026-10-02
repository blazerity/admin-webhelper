"""Справочник УЗ и запись появлений на устройствах."""

from datetime import timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import (
    Device,
    DeviceAccountHistory,
    EndpointAccount,
    RunAs,
    RunStatus,
    RunType,
    ScriptRun,
    Sector,
    SectorAccess,
    SectorRange,
    User,
)
from app.services.account_service import (
    apply_logged_on_user,
    get_or_create_account,
    list_visible_accounts,
    normalize_domain,
    parse_windows_account,
)
from app.services.action_service import list_system_actions
from app.services.discovery_service import WmiInventory
from app.services.ping_service import PingResult, poll_all_sectors
from app.utils import utcnow


def test_parse_windows_account_variants():
    assert parse_windows_account(r"CORP\Alice") == ("CORP", "alice")
    assert parse_windows_account("alice@corp.local") == ("CORP", "alice")
    assert parse_windows_account("bob") == ("", "bob")
    assert parse_windows_account(".\\localuser") == ("", "localuser")
    assert parse_windows_account("") is None
    assert parse_windows_account(None) is None
    assert parse_windows_account("   ") is None


def test_normalize_domain_collapses_dns_to_netbios():
    assert normalize_domain("corp.local") == "CORP"
    assert normalize_domain("CORP") == "CORP"
    assert normalize_domain(".") == ""
    assert normalize_domain("") == ""


def test_upn_and_netbios_share_one_account(app):
    with app.app_context():
        sector = Sector(name="UPN", description="")
        db.session.add(sector)
        db.session.flush()
        device = Device(ip="10.0.0.9", sector_id=sector.id, last_status="online")
        db.session.add(device)
        db.session.flush()

        a = apply_logged_on_user(device, r"CORP\alice")
        db.session.commit()
        b = apply_logged_on_user(device, "alice@corp.local")
        db.session.commit()
        assert a.id == b.id
        assert db.session.query(EndpointAccount).count() == 1
        assert a.domain == "CORP"


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


def test_get_or_create_account_recovers_from_unique_race(app, monkeypatch):
    """Вторая параллельная вставка ловит IntegrityError и возвращает существующую."""
    with app.app_context():
        existing = get_or_create_account(username="race", domain="CORP")
        db.session.commit()
        existing_id = existing.id

        real_find = __import__(
            "app.services.account_service", fromlist=["_find_account"]
        )._find_account
        calls = {"n": 0}

        def flaky_find(domain, username):
            calls["n"] += 1
            # Первый вызов в get_or_create — «ещё нет» (имитация гонки).
            if calls["n"] == 1:
                return None
            return real_find(domain, username)

        monkeypatch.setattr(
            "app.services.account_service._find_account",
            flaky_find,
        )
        recovered = get_or_create_account(username="race", domain="CORP")
        db.session.commit()
        assert recovered.id == existing_id
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

        apply_logged_on_user(device, r"CORP\alice")
        db.session.commit()
        assert db.session.query(DeviceAccountHistory).count() == 1

        other = apply_logged_on_user(device, r"CORP\bob")
        db.session.commit()
        assert other.username == "bob"
        assert device.current_account_id == other.id
        assert db.session.query(DeviceAccountHistory).count() == 2

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


def test_poll_preserves_account_when_wmi_fails(app, monkeypatch):
    with app.app_context():
        sector = Sector(name="KeepAcc", description="")
        sector.ranges.append(SectorRange(cidr="10.9.0.6"))
        db.session.add(sector)
        db.session.flush()
        device = Device(
            ip="10.9.0.6",
            serial_number="KEEP1",
            sector_id=sector.id,
            last_status="online",
        )
        db.session.add(device)
        db.session.flush()
        account = apply_logged_on_user(device, r"CORP\keep")
        db.session.commit()
        account_id = account.id

        monkeypatch.setattr(
            "app.services.ping_service.ping_host",
            lambda ip, timeout_s=1: PingResult("online", 8, "ok"),
        )
        monkeypatch.setattr(
            "app.services.discovery_service.lookup_hostname",
            lambda ip: "n-keep",
        )
        monkeypatch.setattr(
            "app.services.discovery_service.lookup_mac",
            lambda ip: None,
        )
        monkeypatch.setattr(
            "app.services.discovery_service.lookup_wmi_inventory",
            lambda ip: WmiInventory(),  # logged_on_user is None
        )

        poll_all_sectors()
        device = db.session.get(Device, device.id)
        assert device.current_account_id == account_id


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

        alice = db.session.get(User, alice_id)
        carol = db.session.get(User, carol_id)
        assert list_visible_accounts(alice) == []
        visible = list_visible_accounts(carol)
        assert len(visible) == 1
        assert visible[0].username == "dave"


def test_get_visible_account_forbidden_for_outsider(client, app, alice_id):
    with app.app_context():
        sector = Sector(name="Secret", description="")
        sector.access_rules.append(SectorAccess(subject_type="user", subject_name="carol"))
        db.session.add(sector)
        db.session.flush()
        device = Device(ip="10.0.0.11", sector_id=sector.id, last_status="online")
        db.session.add(device)
        db.session.flush()
        account = apply_logged_on_user(device, r"CORP\hidden")
        db.session.commit()
        account_id = account.id

    with client.session_transaction() as session:
        session["_user_id"] = str(alice_id)
    assert client.get(f"/accounts/{account_id}").status_code == 403


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
    assert "CORP\\eve" in listing.get_data(as_text=True)

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


def test_list_system_actions_hides_foreign_orphaned_runs(app, alice_id, carol_id):
    with app.app_context():
        alice = db.session.get(User, alice_id)
        carol = db.session.get(User, carol_id)
        # Запуск без устройства от carol — alice не должна его видеть.
        orphan = ScriptRun(
            user_id=carol.id,
            device_id=None,
            run_type=RunType.COMMAND,
            run_as=RunAs.PSEXEC,
            command_text="secret-orphan-cmd",
            status=RunStatus.SUCCESS,
            started_at=utcnow(),
        )
        db.session.add(orphan)
        db.session.commit()

        with app.test_request_context("/"):
            alice_items = list_system_actions(alice, limit=50)
            assert all(
                "secret-orphan-cmd" not in (item.title or "") for item in alice_items
            )
            carol_items = list_system_actions(carol, limit=50)
            assert any(
                "secret-orphan-cmd" in (item.title or "") for item in carol_items
            )
