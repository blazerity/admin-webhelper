"""admin_audit_log: запись и маршрут."""

from app.extensions import db
from app.models import AdminAuditLog, User
from app.services import audit_service


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)


def test_audit_log_persists_row(app, admin_id):
    with app.app_context():
        admin = db.session.get(User, admin_id)
        row = audit_service.log(
            admin,
            "create",
            "sector",
            42,
            detail="Склад",
        )
        assert row.id is not None
        loaded = db.session.get(AdminAuditLog, row.id)
        assert loaded is not None
        assert loaded.actor_user_id == admin_id
        assert loaded.actor_username == "admin"
        assert loaded.action == "create"
        assert loaded.entity_type == "sector"
        assert loaded.entity_id == "42"
        assert loaded.detail == "Склад"


def test_sector_create_writes_audit(client, app, admin_id):
    _login(client, admin_id)
    response = client.post(
        "/sectors/",
        data={
            "name": "AuditSector",
            "description": "",
            "ranges": "10.9.0.0/24",
            "access_users": "",
            "access_groups": "",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 200}
    with app.app_context():
        rows = audit_service.list_audit_entries(limit=20)
        assert any(
            row.action == "create" and row.entity_type == "sector" and "AuditSector" in row.detail
            for row in rows
        )


def test_script_create_writes_audit(client, app, admin_id):
    _login(client, admin_id)
    response = client.post(
        "/scripts/",
        data={
            "name": "audit-script",
            "description": "",
            "target_os": "windows",
            "interpreter": "powershell",
            "storage": "db",
            "run_as": "psexec",
            "content": "Write-Host audit",
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 200}
    with app.app_context():
        rows = audit_service.list_audit_entries(limit=20)
        assert any(
            row.action == "create"
            and row.entity_type == "script"
            and "audit-script" in row.detail
            for row in rows
        )


def test_admin_settings_poll_writes_audit(client, app, admin_id):
    _login(client, admin_id)
    response = client.post(
        "/admin/settings",
        data={"form": "poll", "poll_interval": "333"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        rows = audit_service.list_audit_entries(limit=20)
        assert any(
            row.action == "update"
            and row.entity_type == "admin_settings"
            and row.entity_id == "poll"
            for row in rows
        )
