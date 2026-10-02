"""Удалённый запуск команды на Windows через pypsexec (SMB/ADMIN$).

Учётка — из настроек пользователя или входа на сайт (Fernet в БД).
Пароль не пишется в лог; локальный shell=True не используется.
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


# Секунды простоя до автозакрытия SMB-сессии.
SESSION_IDLE_SECONDS = 15 * 60


class _RemoteSession:
    """SMB-сессия к машине: reuse по IP+user, cancel бьёт в run_executable."""

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self._lock = threading.Lock()
        self.client = None
        self.ip = ""
        self.secret = ""
        self.user_id: int | None = None
        self.open = False
        self.busy = False
        self.idle_timer: threading.Timer | None = None

    def attach(self, client, ip: str, secret: str, user_id: int | None) -> None:
        with self._lock:
            self.client = client
            self.ip = ip
            self.secret = secret
            self.user_id = user_id
            self.open = True

    def close(self) -> bool:
        """Закрывает службу и SMB. True — сессия была открыта и её трогали."""
        _cancel_idle(self)
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


def acquire_session(run_id: int, user_id: int | None, ip: str) -> tuple[_RemoteSession, bool]:
    """Берёт уже открытую сессию этого пользователя к IP или создаёт новую.

    Возвращает (сессия, reused). Пока идёт команда, сессия помечена busy
    и второму запуску не отдаётся.
    """
    with _sessions_lock:
        for key, session in list(_sessions.items()):
            if (
                session.open
                and not session.busy
                and session.ip == ip
                and user_id is not None
                and session.user_id == user_id
            ):
                _cancel_idle(session)
                session.busy = True
                session.cancel = threading.Event()
                if key != run_id:
                    _sessions.pop(key, None)
                    _sessions[run_id] = session
                return session, True
        session = _sessions.get(run_id)
        if session is None:
            session = _RemoteSession()
            _sessions[run_id] = session
        session.busy = True
        return session, False


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


def discard_if_closed(run_id: int) -> None:
    with _sessions_lock:
        session = _sessions.get(run_id)
        if session is not None and not session.open:
            _sessions.pop(run_id, None)


def session_state(run_id: int, finished: bool) -> str:
    """closed — закрывать нечего; busy — команда ещё идёт; open — кнопку можно нажать."""
    with _sessions_lock:
        session = _sessions.get(run_id)
        if session is None or not session.open:
            return "closed"
        if session.busy and not session.cancel.is_set():
            return "busy"
        if finished or session.cancel.is_set():
            return "open"
        return "busy"


def reset_tracked_sessions() -> None:
    """Сбрасывает учёт сессий между тестами. Удалённые машины не трогает."""
    with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        _cancel_idle(session)


def _arm_idle_close(session: _RemoteSession, run_id: int) -> None:
    def fire() -> None:
        with _sessions_lock:
            current = _sessions.get(run_id)
            if current is not session or not session.open or session.busy:
                return
            session.busy = True
        try:
            session.close()
        finally:
            if session.open:
                session.busy = False
            discard_if_closed(run_id)

    timer = threading.Timer(SESSION_IDLE_SECONDS, fire)
    timer.daemon = True
    with session._lock:
        previous = session.idle_timer
        session.idle_timer = timer
    if previous is not None:
        previous.cancel()
    timer.start()


def _cancel_idle(session: _RemoteSession) -> None:
    with session._lock:
        timer = session.idle_timer
        session.idle_timer = None
    if timer is not None:
        timer.cancel()


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
        # EncodedCommand — чтобы кавычки/переводы строк не развалили аргумент;
        # в script_runs.command_text лежит исходный текст, не эта base64.
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
        # Аргумент удалённого cmd.exe; shell=False — локальный процесс без shell.
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
    session = None
    reused = False
    completed = False
    if run_id is not None:
        session, reused = acquire_session(run_id, user_id, ip)
        if reused:
            client = session.client
            secret = session.secret or secret
    try:
        # Импорт здесь, чтобы тест мог подменить pypsexec.client.Client.
        from pypsexec.client import Client

        if not reused:
            client = Client(ip, username=username, password=secret, encrypt=True)
            logger.info(
                "Удалённый запуск %s на %s от имени %s",
                executable,
                ip,
                "NT AUTHORITY\\SYSTEM" if as_system else "учётки PsExec",
            )
            client.connect()
            client.create_service()
            if session is not None:
                session.attach(client, ip, secret, user_id)
        else:
            logger.debug("Повторная команда %s на %s в открытой сессии", executable, ip)
        pipe_kwargs = {}
        if on_output is not None:
            pipe = _streaming_pipe(on_output, secret)
            # Канал читается в потоке pypsexec — строки в журнале до exit code.
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
        completed = True
        return int(rc), output
    except RemoteExecError:
        raise
    except Exception as exc:
        safe = _scrub(str(exc), secret)
        logger.warning("Удалённый запуск на %s не удался: %s", ip, safe)
        raise RemoteExecError(f"Не удалось выполнить команду на {ip}: {safe}") from None
    finally:
        if session is not None:
            session.busy = False
        keep_open = (
            session is not None
            and completed
            and session.open
            and not session.cancel.is_set()
        )
        if keep_open:
            # Команда кончилась, SMB ещё нужен для «Завершить сессию» / next cmd.
            _arm_idle_close(session, run_id)
        elif session is not None and session.open:
            session.close()
            discard_if_closed(run_id)
        else:
            _cleanup(client, ip, secret)
            if session is not None:
                discard_if_closed(run_id)


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
