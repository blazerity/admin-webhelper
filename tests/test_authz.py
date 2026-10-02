from app.authz import accessible_sectors, user_can_access_device
from app.extensions import db
from app.models import Device, Sector, SectorAccess, User


def _sector(name, users=(), groups=()):
    sector = Sector(name=name, description="")
    for username in users:
        sector.access_rules.append(SectorAccess(subject_type="user", subject_name=username))
    for group in groups:
        sector.access_rules.append(SectorAccess(subject_type="group", subject_name=group))
    db.session.add(sector)
    db.session.commit()
    return sector


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
