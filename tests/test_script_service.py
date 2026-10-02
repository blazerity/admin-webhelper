import threading
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Device, RunAs, RunStatus, RunType, Script, ScriptRun, Sector, User
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
        ping.assert_called_once()
        assert ping.call_args.args[0] == "10.0.0.8"
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


def test_save_script_stores_system_identity(app, admin_id):
    with app.app_context():
        script = save_script(
            name="whoami_system",
            description="",
            target_os="windows",
            interpreter="cmd",
            storage="db",
            content="whoami",
            user_id=admin_id,
            run_as=RunAs.SYSTEM,
        )
        assert script.run_as == RunAs.SYSTEM
        with pytest.raises(ScriptError, match="SYSTEM"):
            save_script(
                name="bad_identity",
                description="",
                target_os="windows",
                interpreter="cmd",
                storage="db",
                content="whoami",
                user_id=admin_id,
                run_as="domain-user",
            )


def test_execute_run_passes_system_account_for_script(app, admin_id):
    with app.app_context():
        device = _device("10.0.0.11")
        script = save_script(
            name="as_system",
            description="",
            target_os="windows",
            interpreter="powershell",
            storage="db",
            content="whoami",
            user_id=admin_id,
            run_as=RunAs.SYSTEM,
        )
        run = ScriptRun(
            script_id=script.id,
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.SCRIPT,
            run_as=RunAs.SYSTEM,
            command_text="whoami",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(run)
        db.session.commit()
        with patch(
            "app.services.script_service.run_remote_script",
            return_value=(0, "nt authority\\system"),
        ) as remote:
            execute_run(app, run.id)
        remote.assert_called_once()
        assert remote.call_args.args[:3] == ("10.0.0.11", "powershell", "whoami")
        assert remote.call_args.kwargs["as_system"] is True
        assert remote.call_args.kwargs["user_id"] == admin_id
        assert remote.call_args.kwargs["run_id"] == run.id
        assert remote.call_args.kwargs["on_output"] is not None
        db.session.expire_all()
        stored = db.session.get(ScriptRun, run.id)
        assert stored.status == RunStatus.SUCCESS
        assert stored.run_as == RunAs.SYSTEM


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


def test_remote_output_reaches_the_log_before_the_command_ends(app, admin_id):
    seen = []

    def fake(ip, command, timeout=120, user_id=None, on_output=None, run_id=None):
        on_output("Reply from 10.2.2.2\n")
        db.session.expire_all()
        stored = db.session.get(ScriptRun, run_id)
        seen.append(stored.log_text)
        return 0, "Reply from 10.2.2.2\n"

    with app.app_context():
        device = _device("10.2.2.2")
        run = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.COMMAND,
            command_text="ping 10.2.2.2",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(run)
        db.session.commit()
        with patch("app.services.script_service.run_remote_command", side_effect=fake):
            execute_run(app, run.id)
        db.session.expire_all()
        stored = db.session.get(ScriptRun, run.id)
        assert seen == ["Reply from 10.2.2.2\n"]
        assert stored.log_text == "Reply from 10.2.2.2\n"
        assert stored.status == RunStatus.SUCCESS


def test_cancel_pending_run_from_the_log_page(client, app, admin_id):
    with app.app_context():
        device = _device("10.2.2.3")
        run = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.SCRIPT,
            command_text="ping -t 10.2.2.3",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(run)
        db.session.commit()
        run_id = run.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
    page = client.get(f"/scripts/runs/{run_id}")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Остановить" in body
    assert 'id="run-close-session"' in body
    assert "hidden" in body
    stopped = client.post(f"/scripts/runs/{run_id}/cancel", follow_redirects=True)
    assert stopped.status_code == 200
    assert "остановлен" in stopped.get_data(as_text=True)
    with app.app_context():
        stored = db.session.get(ScriptRun, run_id)
        assert stored.status == RunStatus.CANCELLED
        assert "Выполнение остановлено." in stored.log_text


def test_close_session_when_automatic_cleanup_failed(app, admin_id):
    from app.services.credential_service import save_remote_admin_credentials

    attempts = {"remove": 0}

    class FakeClient:
        def __init__(self, server, username=None, password=None, port=445, encrypt=True):
            self.removed = False
            self.disconnected = False

        def connect(self):
            return None

        def create_service(self):
            return None

        def run_executable(self, executable, arguments=None, timeout_seconds=0, **kwargs):
            return b"ok", b"", 0

        def remove_service(self):
            attempts["remove"] += 1
            if attempts["remove"] == 1:
                raise RuntimeError("service busy")
            self.removed = True

        def disconnect(self):
            self.disconnected = True

    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        device = _device("10.2.2.4")
        run = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.COMMAND,
            command_text="hostname",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(run)
        db.session.commit()
        with patch("pypsexec.client.Client", FakeClient):
            execute_run(app, run.id)
        from app.services.script_service import run_session_open, close_run_session

        assert run_session_open(run.id, True) is True
        assert close_run_session(run.id) is True
        assert run_session_open(run.id, True) is False
        assert attempts["remove"] == 2


def test_cancel_unblocks_a_running_remote_command(app, admin_id):
    from app.services.credential_service import save_remote_admin_credentials
    from app.services.script_service import cancel_run

    started = threading.Event()
    release = threading.Event()

    class FakeClient:
        def __init__(self, server, username=None, password=None, port=445, encrypt=True):
            self.removed = False

        def connect(self):
            return None

        def create_service(self):
            return None

        def run_executable(self, executable, arguments=None, timeout_seconds=0, **kwargs):
            started.set()
            assert release.wait(5)
            return b"partial", b"", 1

        def remove_service(self):
            self.removed = True
            release.set()

        def disconnect(self):
            return None

    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        device = _device("10.2.2.5")
        run = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.COMMAND,
            command_text="ping -n 30 10.2.2.5",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(run)
        db.session.commit()
        run_id = run.id

        def worker():
            with patch("pypsexec.client.Client", FakeClient):
                execute_run(app, run_id)

        thread = threading.Thread(target=worker)
        thread.start()
        assert started.wait(5)
        cancel_run(run_id)
        thread.join(5)
        assert not thread.is_alive()
        db.session.expire_all()
        stored = db.session.get(ScriptRun, run_id)
        assert stored.status == RunStatus.CANCELLED
        assert "Выполнение остановлено." in stored.log_text
