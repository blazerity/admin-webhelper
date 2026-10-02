from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Device, RunStatus, RunType, ScriptRun, Sector
from app.services.credential_service import (
    get_remote_admin_credentials,
    save_remote_admin_credentials,
)
from app.services.psexec_service import RemoteExecError, run_remote_command, run_remote_script
from app.services.script_service import execute_run


def _fake_client(stdout=b"ok", stderr=None, rc=0, raise_with_password=False):
    created = []

    class FakeClient:
        def __init__(self, server, username=None, password=None, port=445, encrypt=True):
            self.server = server
            self.username = username
            self.password = password
            self.encrypt = encrypt
            self.removed = False
            self.disconnected = False
            self.run_kwargs = {}
            created.append(self)

        def connect(self):
            return None

        def create_service(self):
            return None

        def run_executable(self, executable, arguments=None, timeout_seconds=0, **kwargs):
            self.executable = executable
            self.arguments = arguments
            self.timeout_seconds = timeout_seconds
            self.run_kwargs = kwargs
            if raise_with_password:
                raise RuntimeError(f"smb failed for {self.password}")
            return stdout, stderr, rc

        def remove_service(self):
            self.removed = True

        def disconnect(self):
            self.disconnected = True

    return created, FakeClient


def _device(ip):
    sector = Sector(name=f"Сектор {ip}", description="")
    db.session.add(sector)
    db.session.flush()
    device = Device(ip=ip, sector_id=sector.id, last_status="unknown")
    db.session.add(device)
    db.session.commit()
    return device


def test_decrypted_password_goes_to_client_and_not_into_script_run(app, admin_id):
    secret = "Sup3rSecret"
    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", secret, admin_id)
        created, fake = _fake_client(stdout="Привет".encode("utf-8"), stderr=None, rc=0)
        with patch("pypsexec.client.Client", fake):
            rc, output = run_remote_command("10.1.1.5", "hostname")
        client = created[0]
        assert rc == 0
        assert output == "Привет"
        assert client.password == get_remote_admin_credentials().password == secret
        assert client.username == r"CORP\winadmin"
        assert client.encrypt is True
        assert client.executable == "cmd.exe"
        assert client.arguments == "/c hostname"
        assert client.run_kwargs.get("use_system_account", False) is False
        assert secret not in (client.arguments or "")
        assert client.removed is True
        assert client.disconnected is True

        device = _device("10.1.1.5")
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
        created.clear()
        with patch("pypsexec.client.Client", fake):
            execute_run(app, run.id)
        # execute_run пишет в сессии своего app_context. Эта сессия ещё
        # держит объект со статусом pending, пока его не сбросить.
        db.session.expire_all()
        stored = db.session.get(ScriptRun, run.id)
        assert stored.status == RunStatus.SUCCESS
        assert stored.command_text == "hostname"
        assert stored.log_text == "Привет"
        assert secret not in stored.command_text
        assert secret not in stored.log_text
        assert created[0].password == secret


def test_failure_does_not_leak_password_and_still_cleans_up(app, admin_id):
    secret = "Sup3rSecret"
    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", secret, admin_id)
        created, fake = _fake_client(raise_with_password=True)
        with patch("pypsexec.client.Client", fake):
            with pytest.raises(RemoteExecError) as caught:
                run_remote_command("10.1.1.5", "hostname")
        assert secret not in str(caught.value)
        assert caught.value.__cause__ is None
        assert created[0].removed is True
        assert created[0].disconnected is True


def test_bash_is_rejected_without_opening_a_client(app):
    with app.app_context():
        with patch("pypsexec.client.Client") as client:
            with pytest.raises(RemoteExecError, match="Linux"):
                run_remote_script("10.1.1.5", "bash", "echo hi")
        client.assert_not_called()


def test_empty_command_is_rejected(app):
    with app.app_context():
        with patch("pypsexec.client.Client") as client:
            with pytest.raises(RemoteExecError):
                run_remote_command("10.1.1.5", "   ")
        client.assert_not_called()
