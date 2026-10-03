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


def test_start_run_enqueues_execute_run(app, admin_id):
    with app.app_context():
        device = _device("10.0.0.9")
        user = db.session.get(User, admin_id)
        with patch("app.services.script_service.enqueue_run") as enqueue:
            run = start_run(RunType.PING, user, device, "ping 10.0.0.9")
        enqueue.assert_called_once_with(app, run.id)
        assert run.status == RunStatus.PENDING


def test_start_ping_on_devices_one_commit_and_enqueue(app, admin_id):
    from app.services.script_service import start_ping_on_devices

    with app.app_context():
        devices = [_device("10.0.0.11"), _device("10.0.0.12"), _device("10.0.0.13")]
        user = db.session.get(User, admin_id)
        with (
            patch("app.services.script_service.enqueue_run") as enqueue,
            patch.object(db.session, "commit", wraps=db.session.commit) as commit,
        ):
            batch_id, runs = start_ping_on_devices(user, devices)
        assert batch_id
        assert len(runs) == 3
        assert len({run.batch_id for run in runs}) == 1
        assert commit.call_count == 1
        assert enqueue.call_count == 3
        assert all(run.status == RunStatus.PENDING for run in runs)


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
    assert "Завершить сессию" in body
    assert "Вернуться к компьютеру" in body
    assert "10.2.2.3 — " in body
    assert 'id="run-command"' in body
    close = _button(body, "run-close-session")
    cancel = _button(body, "run-cancel")
    assert "disabled" in close
    assert "disabled" not in cancel
    stopped = client.post(f"/scripts/runs/{run_id}/cancel", follow_redirects=True)
    assert stopped.status_code == 200
    assert "остановлен" in stopped.get_data(as_text=True)
    with app.app_context():
        stored = db.session.get(ScriptRun, run_id)
        assert stored.status == RunStatus.CANCELLED
        assert "Выполнение остановлено." in stored.log_text


def _button(body: str, element_id: str) -> str:
    import re

    match = re.search(rf'<button[^>]*id="{element_id}"[^>]*>', body)
    assert match, element_id
    return match.group(0)


def test_successful_command_leaves_session_until_user_closes(app, admin_id):
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
        from app.services.psexec_service import session_state
        from app.services.script_service import close_run_session

        # Удачная команда не рвёт SMB сама: кнопку «Завершить сессию» можно нажать.
        assert attempts["remove"] == 0
        assert session_state(run.id, True) == "open"
        assert close_run_session(run.id) is True
        assert session_state(run.id, True) == "closed"
        assert attempts["remove"] == 1


def test_failed_close_keeps_the_session_button_active(app, admin_id):
    from app.services.credential_service import save_remote_admin_credentials

    class FakeClient:
        def __init__(self, server, username=None, password=None, port=445, encrypt=True):
            pass

        def connect(self):
            return None

        def create_service(self):
            return None

        def run_executable(self, executable, arguments=None, timeout_seconds=0, **kwargs):
            return b"ok", b"", 0

        def remove_service(self):
            raise RuntimeError("service busy")

        def disconnect(self):
            raise RuntimeError("service busy")

    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        device = _device("10.2.2.41")
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
        from app.services.psexec_service import session_state
        from app.services.script_service import close_run_session

        assert session_state(run.id, True) == "open"
        assert close_run_session(run.id) is True
        assert session_state(run.id, True) == "open"


def test_next_command_reuses_the_open_session(app, admin_id):
    from app.services.credential_service import save_remote_admin_credentials

    created = []

    class FakeClient:
        def __init__(self, server, username=None, password=None, port=445, encrypt=True):
            self.removed = False
            created.append(self)

        def connect(self):
            return None

        def create_service(self):
            return None

        def run_executable(self, executable, arguments=None, timeout_seconds=0, **kwargs):
            self.executable = executable
            self.arguments = arguments
            return b"ok", b"", 0

        def remove_service(self):
            self.removed = True

        def disconnect(self):
            return None

    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        device = _device("10.2.2.42")
        first = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.COMMAND,
            command_text="hostname",
            status=RunStatus.PENDING,
            log_text="",
        )
        second = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.COMMAND,
            command_text="whoami",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(first)
        db.session.add(second)
        db.session.commit()
        with patch("pypsexec.client.Client", FakeClient):
            execute_run(app, first.id)
            execute_run(app, second.id)
        assert len(created) == 1
        assert created[0].arguments == "/c whoami"
        assert created[0].removed is False
        from app.services.psexec_service import session_state
        from app.services.script_service import close_run_session

        assert session_state(first.id, True) == "closed"
        assert session_state(second.id, True) == "open"
        assert close_run_session(second.id) is True
        assert created[0].removed is True


def test_finished_run_page_enables_end_session(client, app, admin_id):
    from app.services.credential_service import save_remote_admin_credentials

    class FakeClient:
        def __init__(self, server, username=None, password=None, port=445, encrypt=True):
            pass

        def connect(self):
            return None

        def create_service(self):
            return None

        def run_executable(self, executable, arguments=None, timeout_seconds=0, **kwargs):
            return b"ok", b"", 0

        def remove_service(self):
            return None

        def disconnect(self):
            return None

    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        device = _device("10.2.2.43")
        device.hostname = "PC-LAB"
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
        run_id = run.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
    page = client.get(f"/scripts/runs/{run_id}")
    body = page.get_data(as_text=True)
    assert "PC-LAB — " in body
    assert "disabled" not in _button(body, "run-close-session")
    assert "disabled" in _button(body, "run-cancel")
    assert "CORP\\winadmin" in body
    assert "сессия: <span id=\"run-session-state\">открыта</span>" in body


def test_follow_up_command_leaves_the_previous_run_finished(client, app, admin_id):
    import time

    from app.services.credential_service import save_remote_admin_credentials

    class FakeClient:
        def __init__(self, server, username=None, password=None, port=445, encrypt=True):
            pass

        def connect(self):
            return None

        def create_service(self):
            return None

        def run_executable(self, executable, arguments=None, timeout_seconds=0, **kwargs):
            return b"ok", b"", 0

        def remove_service(self):
            return None

        def disconnect(self):
            return None

    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        device = _device("10.2.2.44")
        first = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.COMMAND,
            command_text="hostname",
            status=RunStatus.PENDING,
            log_text="",
        )
        db.session.add(first)
        db.session.commit()
        device_id = device.id
        first_id = first.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
    with patch("pypsexec.client.Client", FakeClient):
        with app.app_context():
            execute_run(app, first_id)
        response = client.post(
            f"/devices/{device_id}/command",
            data={"command": "whoami"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        deadline = time.time() + 5
        with app.app_context():
            while time.time() < deadline:
                db.session.expire_all()
                rows = (
                    ScriptRun.query.filter_by(device_id=device_id)
                    .order_by(ScriptRun.id)
                    .all()
                )
                if len(rows) >= 2 and rows[-1].status in RunStatus.FINISHED:
                    break
                time.sleep(0.05)
            db.session.expire_all()
            rows = (
                ScriptRun.query.filter_by(device_id=device_id)
                .order_by(ScriptRun.id)
                .all()
            )
            assert [row.command_text for row in rows] == ["hostname", "whoami"]
            assert [row.status for row in rows] == [RunStatus.SUCCESS, RunStatus.SUCCESS]


def test_run_page_names_the_computer_and_lists_earlier_launches(client, app, admin_id):
    with app.app_context():
        device = _device("10.9.9.1")
        device.hostname = "PC-OFFICE"
        device.mac = "AA:BB:CC:DD:EE:FF"
        earlier = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.SCRIPT,
            command_text="Get-Disk",
            status=RunStatus.SUCCESS,
            log_text="ok",
        )
        script = Script(name="Диски", description="", target_os="windows", interpreter="powershell")
        db.session.add(script)
        db.session.flush()
        earlier.script_id = script.id
        current = ScriptRun(
            device_id=device.id,
            user_id=admin_id,
            run_type=RunType.COMMAND,
            command_text="whoami",
            status=RunStatus.SUCCESS,
            log_text="corp\\admin",
        )
        db.session.add(earlier)
        db.session.add(current)
        db.session.commit()
        current_id = current.id
        when = current.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
    page = client.get(f"/scripts/runs/{current_id}")
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert f"PC-OFFICE — {when}" in body
    assert "Вернуться к компьютеру" in body
    assert "Что запускали на этом компьютере" in body
    assert "Диски" in body
    assert "AA:BB:CC:DD:EE:FF" in body
    assert "10.9.9.1" in body
    command = body.split('id="run-command"', 1)[1]
    assert "disabled" not in command.split(">", 1)[0]


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
