"""In-app notifications API and script_failed helper."""

from app.extensions import db
from app.models import (
    Device,
    DeviceStatus,
    Notification,
    NotificationKind,
    RunStatus,
    RunType,
    Script,
    ScriptRun,
    Sector,
)
from app.services import notification_service
from app.utils import utcnow


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_list_notifications_empty(client, app, admin_id):
    _login(client, admin_id)
    response = client.get("/api/notifications")
    assert response.status_code == 200
    data = response.get_json()
    assert data["items"] == []
    assert data["unread"] == 0


def test_list_and_mark_read(client, app, admin_id, alice_id):
    with app.app_context():
        notification_service.create_notification(
            admin_id,
            NotificationKind.DEVICE_OFFLINE,
            "A offline",
            body="body",
            link_url="/devices/1",
        )
        notification_service.create_notification(
            admin_id,
            NotificationKind.POLL_ERROR,
            "Poll failed",
            body="",
            link_url="/admin/settings",
        )
        # Чужое — не в ленте admin.
        notification_service.create_notification(
            alice_id,
            NotificationKind.DEVICE_OFFLINE,
            "Alice only",
            link_url="/devices/2",
        )

    _login(client, admin_id)
    response = client.get("/api/notifications")
    assert response.status_code == 200
    data = response.get_json()
    assert data["unread"] == 2
    assert len(data["items"]) == 2
    assert all(item["unread"] for item in data["items"])

    first_id = data["items"][0]["id"]
    mark = client.post("/api/notifications/read", json={"ids": [first_id]})
    assert mark.status_code == 200
    assert mark.get_json()["updated"] == 1

    again = client.get("/api/notifications").get_json()
    assert again["unread"] == 1

    all_mark = client.post("/api/notifications/read", json={"all": True})
    assert all_mark.status_code == 200
    assert all_mark.get_json()["updated"] == 1
    final = client.get("/api/notifications").get_json()
    assert final["unread"] == 0


def test_mark_read_requires_login(client):
    response = client.get("/api/notifications")
    assert response.status_code in {302, 401}


def test_notify_script_failed(app, admin_id, alice_id):
    with app.app_context():
        sector = Sector(name="FailLab", description="")
        db.session.add(sector)
        db.session.flush()
        device = Device(
            ip="10.1.1.9",
            hostname="n-fail",
            sector_id=sector.id,
            last_status=DeviceStatus.ONLINE,
            last_seen=utcnow(),
        )
        db.session.add(device)
        script = Script(
            name="fail-script",
            description="",
            target_os="windows",
            interpreter="powershell",
            storage="db",
            content="Write-Host x",
            created_by_id=admin_id,
        )
        db.session.add(script)
        db.session.flush()
        run = ScriptRun(
            script_id=script.id,
            device_id=device.id,
            user_id=alice_id,
            run_type=RunType.SCRIPT,
            command_text="x",
            status=RunStatus.FAILED,
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        db.session.add(run)
        db.session.commit()

        note = notification_service.notify_script_failed(run)
        assert note is not None
        assert note.user_id == admin_id
        assert note.kind == NotificationKind.SCRIPT_FAILED
        assert note.link_url == f"/scripts/runs/{run.id}"

        # Dedupe same run.
        assert notification_service.notify_script_failed(run) is None
        assert Notification.query.filter_by(kind=NotificationKind.SCRIPT_FAILED).count() == 1


def test_notify_script_failed_skips_without_author(app, alice_id):
    with app.app_context():
        script = Script(
            name="orphan-script",
            description="",
            target_os="windows",
            interpreter="powershell",
            storage="db",
            content="x",
            created_by_id=None,
        )
        db.session.add(script)
        db.session.flush()
        run = ScriptRun(
            script_id=script.id,
            device_id=None,
            user_id=alice_id,
            run_type=RunType.SCRIPT,
            command_text="x",
            status=RunStatus.FAILED,
            started_at=utcnow(),
        )
        db.session.add(run)
        db.session.commit()
        assert notification_service.notify_script_failed(run) is None
