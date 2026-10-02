"""Удалённый запуск команды на Windows-машине из инвентаря.

Сервис bAWH стоит на Debian. psexec.exe — программа Windows, её нельзя
запустить на самом сервере приложения, поэтому она не является средой
выполнения. Пример impacket.examples.psexec тоже не берём: вместе с ним
подтягивается весь набор примеров Impacket. pypsexec — узкая библиотека
запуска через службу SMB, её и вызываем.

На целевой машине нужно:
- открытый TCP 445;
- общая папка ADMIN$;
- учётная запись, которая входит в локальные администраторы этой машины.

TODO: положите эту учётку в форму /admin/settings или в переменные
PSEXEC_USERNAME, PSEXEC_DOMAIN, PSEXEC_PASSWORD. Пароль в базе — шифротекст
Fernet, его пишет credential_service. Ключ FERNET_KEY остаётся в окружении
процесса, в код, в шаблон и в script_runs он не попадает.

Пароль живёт только в аргументе Client на время сессии. В лог и в текст
исключения его не кладём. use_system_account не передаём: процесс идёт
от этой учётки, а не от NT AUTHORITY\\SYSTEM. Локальный shell на хосте
Flask не открывается (shell=True нет): pypsexec сам по себе его не зовёт.
"""

import base64
import logging

from flask import current_app

from app.services.credential_service import get_remote_admin_credentials
from app.services.net_utils import assert_public_ipv4

logger = logging.getLogger(__name__)


class RemoteExecError(RuntimeError):
    """Команду на удалённой машине выполнить нельзя. Текст безопасен для журнала."""


def run_remote_command(ip: str, command: str, timeout: int = 120) -> tuple[int, str]:
    """Запускает cmd.exe /c на одной Windows-машине. Возвращает (код, вывод)."""
    checked_ip = assert_public_ipv4(ip)
    text = (command or "").strip()
    if not text:
        raise RemoteExecError("Команда пустая.")
    limit = int(current_app.config.get("MAX_REMOTE_COMMAND_CHARS", 4_000))
    if len(text) > limit:
        raise RemoteExecError(f"Команда длиннее {limit} символов.")
    # Строка — аргумент удалённого cmd.exe, не локальной оболочки.
    return _run(checked_ip, "cmd.exe", f"/c {text}", timeout)


def run_remote_script(
    ip: str,
    interpreter: str,
    body: str,
    timeout: int = 180,
) -> tuple[int, str]:
    """Запускает текст из библиотеки. bash в v1 отклоняется: PsExec — только Windows."""
    checked_ip = assert_public_ipv4(ip)
    kind = (interpreter or "").strip().lower()
    if kind == "bash":
        raise RemoteExecError(
            "Удалённый Linux в v1 не реализован: PsExec работает только с Windows."
        )
    script = body or ""
    if not script.strip():
        raise RemoteExecError("Текст скрипта пустой.")
    if kind == "powershell":
        # EncodedCommand нужен только чтобы кавычки и переводы строк
        # не развалили аргумент. Это не маскировка: в script_runs.command_text
        # лежит исходный текст из библиотеки, а не эта base64-строка.
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        arguments = (
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass "
            f"-EncodedCommand {encoded}"
        )
        return _run(checked_ip, "powershell.exe", arguments, timeout)
    if kind == "cmd":
        # Тело уходит аргументом удалённого cmd.exe. shell=False относится
        # к локальному процессу; pypsexec локальный shell не использует.
        return _run(checked_ip, "cmd.exe", f"/c {script}", timeout)
    raise RemoteExecError(f"Неизвестный интерпретатор: {interpreter}")


def _run(ip: str, executable: str, arguments: str, timeout: int) -> tuple[int, str]:
    creds = get_remote_admin_credentials()
    secret = creds.password
    username = rf"{creds.domain}\{creds.username}" if creds.domain else creds.username
    client = None
    try:
        # Импорт здесь, чтобы тест мог подменить pypsexec.client.Client.
        from pypsexec.client import Client

        client = Client(ip, username=username, password=secret, encrypt=True)
        logger.info("Удалённый запуск %s на %s", executable, ip)
        client.connect()
        client.create_service()
        stdout, stderr, rc = client.run_executable(
            executable,
            arguments=arguments,
            timeout_seconds=timeout,
        )
        if rc is None:
            rc = 1
        output = _scrub(_combine(stdout, stderr), secret)
        return int(rc), output
    except RemoteExecError:
        raise
    except Exception as exc:
        safe = _scrub(str(exc), secret)
        logger.warning("Удалённый запуск на %s не удался: %s", ip, safe)
        raise RemoteExecError(f"Не удалось выполнить команду на {ip}: {safe}") from None
    finally:
        _cleanup(client, ip, secret)


def _cleanup(client, ip: str, secret: str) -> None:
    if client is None:
        return
    try:
        client.remove_service()
    except Exception as exc:
        logger.warning(
            "Не удалось удалить службу PAExec на %s: %s",
            ip,
            _scrub(str(exc), secret),
        )
    try:
        client.disconnect()
    except Exception as exc:
        logger.warning(
            "Не удалось закрыть SMB-сессию к %s: %s",
            ip,
            _scrub(str(exc), secret),
        )


def _decode(data) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _combine(stdout, stderr) -> str:
    out = _decode(stdout)
    err = _decode(stderr)
    if not err:
        return out
    if not out:
        return err
    if out.endswith("\n"):
        return out + err
    return out + "\n" + err


def _scrub(text: str, secret: str) -> str:
    if not text:
        return ""
    if secret:
        return text.replace(secret, "***")
    return text
