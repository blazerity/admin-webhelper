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

Учётка берётся у пользователя, который запустил команду. Если в его
настройках поля PsExec пустые, используется вход на сайт. Пароль в базе —
шифротекст Fernet, его пишет credential_service. Ключ FERNET_KEY остаётся
в окружении процесса, в код, в шаблон и в script_runs он не попадает.

Пароль живёт только в аргументе Client на время сессии. В лог и в текст
исключения его не кладём. SMB-сессию всегда открывает учётка из настроек:
без неё службу на ADMIN$ не создать. as_system=True передаёт
use_system_account, и уже сам процесс на целевой машине идёт от
NT AUTHORITY\\SYSTEM, а не от этой учётки и не от пользователя сайта.
По умолчанию флаг выключен. Локальный shell на хосте Flask не
открывается (shell=True нет): pypsexec сам по себе его не зовёт.
"""

import base64
import logging
import threading

from flask import current_app

from app.services.credential_service import get_remote_admin_credentials
from app.services.net_utils import assert_public_ipv4

logger = logging.getLogger(__name__)


class RemoteExecError(RuntimeError):
    """Команду на удалённой машине выполнить нельзя. Текст безопасен для журнала."""


class _RemoteSession:
    """Одна SMB-сессия PsExec, пока идёт запуск.

    Кнопка «Остановить» и кнопка «Закрыть сессию» бьют в этот объект
    из HTTP-потока, пока run_executable ещё сидит в чтении канала.
    """

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self._lock = threading.Lock()
        self.client = None
        self.ip = ""
        self.secret = ""
        self.open = False

    def attach(self, client, ip: str, secret: str) -> None:
        with self._lock:
            self.client = client
            self.ip = ip
            self.secret = secret
            self.open = True

    def close(self) -> bool:
        """Закрывает службу и SMB. True — сессия была открыта и её трогали."""
        with self._lock:
            if not self.open or self.client is None:
                return False
            client, ip, secret = self.client, self.ip, self.secret
            if _cleanup(client, ip, secret):
                self.open = False
                self.client = None
            return True


_sessions_lock = threading.Lock()
_sessions: dict[int, _RemoteSession] = {}


def begin_session(run_id: int) -> _RemoteSession:
    with _sessions_lock:
        session = _sessions.get(run_id)
        if session is None:
            session = _RemoteSession()
            _sessions[run_id] = session
        return session


def cancel_remote(run_id: int) -> bool:
    """Просит процесс остановиться и закрывает сессию, если она есть."""
    with _sessions_lock:
        session = _sessions.get(run_id)
    if session is None:
        return False
    session.cancel.set()
    session.close()
    return True


def close_remote(run_id: int) -> bool:
    """Повторно закрывает сессию, если автоматическая уборка не удалась."""
    with _sessions_lock:
        session = _sessions.get(run_id)
    if session is None:
        return False
    attempted = session.close()
    discard_if_closed(run_id)
    return attempted


def was_cancelled(run_id: int) -> bool:
    with _sessions_lock:
        session = _sessions.get(run_id)
    return session is not None and session.cancel.is_set()


def session_still_open(run_id: int) -> bool:
    with _sessions_lock:
        session = _sessions.get(run_id)
    return session is not None and session.open


def discard_if_closed(run_id: int) -> None:
    with _sessions_lock:
        session = _sessions.get(run_id)
        if session is not None and not session.open:
            _sessions.pop(run_id, None)


def show_close_button(run_id: int, finished: bool) -> bool:
    """Кнопка нужна, когда сессия жива после остановки или после конца запуска."""
    with _sessions_lock:
        session = _sessions.get(run_id)
    if session is None or not session.open:
        return False
    return finished or session.cancel.is_set()


def run_remote_command(
    ip: str,
    command: str,
    timeout: int = 120,
    user_id: int | None = None,
    on_output=None,
    run_id: int | None = None,
) -> tuple[int, str]:
    """Запускает cmd.exe /c на одной Windows-машине. Возвращает (код, вывод)."""
    checked_ip = assert_public_ipv4(ip)
    text = (command or "").strip()
    if not text:
        raise RemoteExecError("Команда пустая.")
    limit = int(current_app.config.get("MAX_REMOTE_COMMAND_CHARS", 4_000))
    if len(text) > limit:
        raise RemoteExecError(f"Команда длиннее {limit} символов.")
    # Строка — аргумент удалённого cmd.exe, не локальной оболочки.
    return _run(
        checked_ip,
        "cmd.exe",
        f"/c {text}",
        timeout,
        user_id=user_id,
        on_output=on_output,
        run_id=run_id,
    )


def run_remote_script(
    ip: str,
    interpreter: str,
    body: str,
    timeout: int = 180,
    as_system: bool = False,
    user_id: int | None = None,
    on_output=None,
    run_id: int | None = None,
) -> tuple[int, str]:
    """Запускает текст из библиотеки. bash в v1 отклоняется: PsExec — только Windows.

    as_system=True — процесс на целевой машине от NT AUTHORITY\\SYSTEM.
    Учётка из настроек при этом всё равно нужна, чтобы открыть SMB.
    """
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
        return _run(
            checked_ip,
            "powershell.exe",
            arguments,
            timeout,
            as_system=as_system,
            user_id=user_id,
            on_output=on_output,
            run_id=run_id,
        )
    if kind == "cmd":
        # Тело уходит аргументом удалённого cmd.exe. shell=False относится
        # к локальному процессу; pypsexec локальный shell не использует.
        return _run(
            checked_ip,
            "cmd.exe",
            f"/c {script}",
            timeout,
            as_system=as_system,
            user_id=user_id,
            on_output=on_output,
            run_id=run_id,
        )
    raise RemoteExecError(f"Неизвестный интерпретатор: {interpreter}")


def _run(
    ip: str,
    executable: str,
    arguments: str,
    timeout: int,
    as_system: bool = False,
    user_id: int | None = None,
    on_output=None,
    run_id: int | None = None,
) -> tuple[int, str]:
    if user_id is None:
        raise RemoteExecError("Неизвестно, от чьего имени запускать PsExec.")
    creds = get_remote_admin_credentials(user_id)
    secret = creds.password
    username = rf"{creds.domain}\{creds.username}" if creds.domain else creds.username
    client = None
    session = begin_session(run_id) if run_id is not None else None
    try:
        # Импорт здесь, чтобы тест мог подменить pypsexec.client.Client.
        from pypsexec.client import Client

        client = Client(ip, username=username, password=secret, encrypt=True)
        if session is not None:
            session.attach(client, ip, secret)
        logger.info(
            "Удалённый запуск %s на %s от имени %s",
            executable,
            ip,
            "NT AUTHORITY\\SYSTEM" if as_system else "учётки PsExec",
        )
        client.connect()
        client.create_service()
        pipe_kwargs = {}
        if on_output is not None:
            pipe = _streaming_pipe(on_output, secret)
            # pypsexec сам создаёт экземпляр и читает канал в отдельном потоке,
            # поэтому строка попадает в журнал ещё до кода возврата.
            pipe_kwargs["stdout"] = pipe
            pipe_kwargs["stderr"] = pipe
        stdout, stderr, rc = client.run_executable(
            executable,
            arguments=arguments,
            timeout_seconds=timeout,
            use_system_account=bool(as_system),
            **pipe_kwargs,
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
        if session is not None:
            session.close()
        else:
            _cleanup(client, ip, secret)


def _streaming_pipe(on_output, secret: str):
    """Класс канала: pypsexec вызывает его как stdout(tree, pipe_name)."""
    from pypsexec.pipe import OutputPipe

    class StreamingPipe(OutputPipe):
        def __init__(self, tree, name):
            self._parts: list[bytes] = []
            self._parts_lock = threading.Lock()
            super().__init__(tree, name)

        def handle_output(self, output):
            if not output:
                return
            with self._parts_lock:
                self._parts.append(output)
            text = _scrub(_decode(output), secret)
            if text:
                on_output(text)

        def get_output(self):
            with self._parts_lock:
                return b"".join(self._parts)

    return StreamingPipe


def _cleanup(client, ip: str, secret: str) -> bool:
    """True, если и службу, и SMB удалось закрыть."""
    if client is None:
        return True
    ok = True
    try:
        client.remove_service()
    except Exception as exc:
        ok = False
        logger.warning(
            "Не удалось удалить службу PAExec на %s: %s",
            ip,
            _scrub(str(exc), secret),
        )
    try:
        client.disconnect()
    except Exception as exc:
        ok = False
        logger.warning(
            "Не удалось закрыть SMB-сессию к %s: %s",
            ip,
            _scrub(str(exc), secret),
        )
    return ok


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
