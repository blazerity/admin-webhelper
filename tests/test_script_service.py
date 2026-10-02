from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Device, RunStatus, RunType, Script, ScriptRun, Sector, User
from app.services.script_service import (
    ScriptError,
    execute_run,
    get_script_body,
    save_script,
    start_run,
)


def _device(ip="10.0.0.8"):
    sector = Sector(name=f"Сектор {ip}", description="")
    db.session.add(sector)
    db.session.flush()
    device = Device(ip=ip, sector_id=sector.id, last_status="unknown")
    db.session.add(device)
    db.session.commit()
    return device


def test_execute_run_ping_updates_status_and_log(app, admin_id):
    ping_service = pytest.importorskip("app.services.ping_service")
    with app.app_context():
        device = _device()
        run = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.PING,
            command_text="ping 10.0.0.8",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(run)
        db.session.commit()
        result = ping_service.PingResult("online", 4, "Reply from 10.0.0.8")
        with patch("app.services.ping_service.ping_host", return_value=result) as ping:
            execute_run(app, run.id)
        ping.assert_called_once_with("10.0.0.8")
        db.session.expire_all()
        stored = db.session.get(ScriptRun, run.id)
        assert stored.status == RunStatus.SUCCESS
        assert "Reply from 10.0.0.8" in stored.log_text
        assert stored.finished_at is not None


def test_filesystem_save_rejects_name_that_escapes_library(app, admin_id, tmp_path):
    library = tmp_path / "library"
    marker = "bawh_escape_outside"
    app.config["SCRIPT_LIBRARY_DIR"] = str(library)
    with app.app_context():
        with pytest.raises(ScriptError):
            save_script(
                name=f"../../{marker}",
                description="",
                target_os="windows",
                interpreter="powershell",
                storage="filesystem",
                content="Get-Date\n",
                user_id=admin_id,
            )
        assert db.session.query(Script).count() == 0
    assert list(tmp_path.rglob(marker)) == []
    assert not (tmp_path / marker).exists()
    assert not (tmp_path.parent / marker).exists()
    assert not (tmp_path.parent.parent / marker).exists()


def test_filesystem_save_writes_inside_library(app, admin_id, tmp_path):
    library = tmp_path / "library"
    app.config["SCRIPT_LIBRARY_DIR"] = str(library)
    body = "Get-PSDrive\n"
    with app.app_context():
        script = save_script(
            name="check_disk",
            description="диски",
            target_os="windows",
            interpreter="powershell",
            storage="filesystem",
            content=body,
            user_id=admin_id,
        )
        assert script.file_path == "check_disk.ps1"
        assert script.content is None
        assert ".." not in script.file_path
        full = library / script.file_path
        assert full.is_file()
        assert full.resolve().is_relative_to(library.resolve())
        assert get_script_body(script) == body


def test_command_route_forbidden_for_alice(client, alice_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(alice_id)
    response = client.post("/devices/1/command", data={"command": "whoami"})
    assert response.status_code == 403


def test_start_run_hands_the_id_to_a_daemon_thread(app, admin_id):
    with app.app_context():
        device = _device("10.0.0.9")
        user = db.session.get(User, admin_id)
        with patch("app.services.script_service.threading.Thread") as thread_cls:
            run = start_run(RunType.PING, user, device, "ping 10.0.0.9")
        kwargs = thread_cls.call_args.kwargs
        assert kwargs["daemon"] is True
        assert kwargs["target"] is execute_run
        assert kwargs["args"] == (app, run.id)
        thread_cls.return_value.start.assert_called_once()
        assert run.status == RunStatus.PENDING
