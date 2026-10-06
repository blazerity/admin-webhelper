"""PowerShell через pypsexec: файл в ADMIN$ + cmd.exe -File, stdout в канал."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.credential_service import RemoteAdminCredentials
from app.services import psexec_service


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, *args, **kwargs):
        FakeClient.instances.append(self)
        self.calls: list[dict] = []
        self.session = object()
        self.connection = MagicMock(server_name="10.10.21.67", max_write_size=65536)

    def connect(self):
        return None

    def create_service(self):
        return None

    def remove_service(self):
        return None

    def disconnect(self):
        return None

    def run_executable(self, executable, arguments=None, **kwargs):
        self.calls.append(
            {"executable": executable, "arguments": arguments, **kwargs}
        )
        return b"agent absent\n", b"", 0


class PowershellLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.instances.clear()
        psexec_service.reset_tracked_sessions()

    def tearDown(self) -> None:
        psexec_service.reset_tracked_sessions()

    def test_file_cmd_streams_powershell_stdout(self) -> None:
        args = psexec_service._powershell_file_cmd("bawh_abc")
        self.assertTrue(args.startswith("/v:on /c "))
        self.assertIn(r"-File C:\Windows\Temp\bawh_abc.ps1", args)
        self.assertIn(r"<nul 2>&1", args)
        self.assertNotIn(".out", args)
        self.assertNotIn(" type ", args)
        self.assertNotIn("EncodedCommand", args)

    def test_wrap_powershell_keeps_user_script(self) -> None:
        wrapped = psexec_service.wrap_powershell_script("Write-Output hi")
        self.assertIn("AutoFlush", wrapped)
        self.assertTrue(wrapped.endswith("Write-Output hi\n"))
        self.assertLess(wrapped.find("AutoFlush"), wrapped.find("Write-Output hi"))

    def test_write_admin_file_rejects_parent_path(self) -> None:
        with self.assertRaises(psexec_service.RemoteExecError):
            psexec_service._write_admin_file(FakeClient(), r"Temp\..\evil.ps1", b"x")

    @patch("app.services.psexec_service._write_admin_file")
    @patch("app.services.psexec_service.get_remote_admin_credentials")
    def test_powershell_runs_via_cmd_file(self, mock_creds, mock_write) -> None:
        mock_creds.return_value = RemoteAdminCredentials("u", "CORP", "secret")
        with patch("pypsexec.client.Client", FakeClient):
            rc, output = psexec_service.run_remote_script(
                "10.10.21.67",
                "powershell",
                "Write-Output hi",
                user_id=7,
                as_system=True,
            )
        self.assertEqual(rc, 0)
        self.assertIn("agent absent", output)
        mock_write.assert_called_once()
        client, relative, payload = mock_write.call_args[0]
        self.assertTrue(relative.startswith(r"Temp\bawh_"))
        self.assertTrue(relative.endswith(".ps1"))
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"AutoFlush", payload)
        self.assertIn(b"Write-Output hi", payload)
        call = FakeClient.instances[-1].calls[0]
        self.assertEqual(call["executable"], "cmd.exe")
        self.assertIn("-File", call["arguments"])
        self.assertIn("<nul 2>&1", call["arguments"])
        self.assertNotIn("EncodedCommand", call["arguments"])
        self.assertTrue(call["use_system_account"])

    def test_pipe_broken_detection(self) -> None:
        self.assertTrue(
            psexec_service._is_pipe_broken(
                "Received unexpected status from the server: "
                "The pipe operation has failed because the other end of the "
                "pipe has been closed. (3221225803) STATUS_PIPE_BROKEN: 0xc000014b"
            )
        )
        self.assertFalse(psexec_service._is_pipe_broken("access denied"))
