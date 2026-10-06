"""Удалённый запуск команды на Windows через pypsexec (SMB/ADMIN$).

Учётка — из настроек пользователя или входа на сайт (Fernet в БД).
Пароль не пишется в лог; локальный shell=True не используется.

PowerShell не запускаем как executable с -EncodedCommand: powershell.exe
закрывает named pipe PAExec (STATUS_PIPE_BROKEN / 0xc000014b). Текст кладём
в ADMIN$\\Temp и гоняем через cmd.exe -File: stdout идёт в канал cmd, журнал
в UI дописывается по мере вывода (on_output), а не после exit.
"""

import logging
import threading
import uuid

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
    timeout: int = 600,
    as_system: bool = False,
    user_id: int | None = None,
    on_output=None,
    run_id: int | None = None,
) -> tuple[int, str]:
    """Запускает текст из библиотеки. bash в v1 отклоняется: PsExec — только Windows.

    PowerShell пишется в ADMIN$\\Temp и запускается через cmd.exe -File:
    powershell.exe с -EncodedCommand закрывает pipe PAExec (STATUS_PIPE_BROKEN).
    stdout не прячем в файл — иначе журнал молчит до конца скрипта.

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
        stem = f"bawh_{uuid.uuid4().hex[:12]}"
        remote_ps1 = rf"Temp\{stem}.ps1"
        payload = wrap_powershell_script(script).encode("utf-8-sig")

        def prepare(client) -> None:
            _write_admin_file(client, remote_ps1, payload)

        return _run(
            checked_ip,
            "cmd.exe",
            _powershell_file_cmd(stem),
            timeout,
            as_system=as_system,
            user_id=user_id,
            on_output=on_output,
            run_id=run_id,
            prepare=prepare,
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


# Буфер stdout у powershell.exe на pipe — иначе Write-Output копится до exit.
_POWERSHELL_HOST_PREAMBLE = (
    "$ProgressPreference = 'SilentlyContinue'\r\n"
    "try {\r\n"
    "  $enc = New-Object System.Text.UTF8Encoding $false\r\n"
    "  [Console]::OutputEncoding = $enc\r\n"
    "  $sw = New-Object System.IO.StreamWriter("
    "[Console]::OpenStandardOutput(), $enc, 16)\r\n"
    "  $sw.AutoFlush = $true\r\n"
    "  [Console]::SetOut($sw)\r\n"
    "} catch {}\r\n"
)


def wrap_powershell_script(script: str) -> str:
    """Префикс live-журнала + текст из библиотеки. BOM добавляет encode utf-8-sig."""
    body = (script or "").lstrip("\ufeff")
    if not body.endswith("\n"):
        body += "\n"
    return _POWERSHELL_HOST_PREAMBLE + body


def _powershell_file_cmd(stem: str) -> str:
    """cmd держит канал PAExec; powershell -File пишет stdout сразу в pipe."""
    ps1 = rf"C:\Windows\Temp\{stem}.ps1"
    return (
        "/v:on /c "
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-File {ps1} <nul 2>&1 "
        "& set BAWHR=!ERRORLEVEL! "
        f"& del /f /q {ps1} "
        "& exit /b !BAWHR!"
    )


def _write_admin_file(client, relative: str, data: bytes) -> None:
    """Пишет файл в ADMIN$ (C:\\Windows\\...) уже открытой SMB-сессии pypsexec."""
    from smbprotocol.open import (
        CreateDisposition,
        CreateOptions,
        FileAttributes,
        FilePipePrinterAccessMask,
        ImpersonationLevel,
        Open,
        ShareAccess,
    )
    from smbprotocol.tree import TreeConnect

    name = (relative or "").replace("/", "\\").lstrip("\\")
    if not name or ".." in name.split("\\"):
        raise RemoteExecError("Некорректный путь временного файла скрипта.")
    tree = TreeConnect(client.session, rf"\\{client.connection.server_name}\ADMIN$")
    tree.connect()
    try:
        handle = Open(tree, name)
        handle.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.FILE_WRITE_DATA
            | FilePipePrinterAccessMask.FILE_READ_DATA,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
            ShareAccess.FILE_SHARE_READ,
            CreateDisposition.FILE_OVERWRITE_IF,
            CreateOptions.FILE_NON_DIRECTORY_FILE,
        )
        chunk = int(getattr(client.connection, "max_write_size", 0) or 65536)
        offset = 0
        while offset < len(data):
            handle.write(data[offset : offset + chunk], offset)
            offset += chunk
        handle.close(False)
    finally:
        tree.disconnect()


def _run(
    ip: str,
    executable: str,
    arguments: str,
    timeout: int,
    as_system: bool = False,
    user_id: int | None = None,
    on_output=None,
    run_id: int | None = None,
    prepare=None,
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
        if prepare is not None:
            try:
                prepare(client)
            except RemoteExecError:
                raise
            except Exception as exc:
                safe = _scrub(str(exc), secret)
                raise RemoteExecError(
                    f"Не удалось записать скрипт на {ip}: {safe}"
                ) from None
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
        if _is_pipe_broken(safe):
            raise RemoteExecError(
                f"Не удалось выполнить команду на {ip}: канал PsExec закрыт "
                f"(STATUS_PIPE_BROKEN). {safe}"
            ) from None
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
            self._drop_clixml = False
            super().__init__(tree, name)

        def handle_output(self, output):
            if not output:
                return
            with self._parts_lock:
                self._parts.append(output)
                dropping = self._drop_clixml
            if dropping:
                return
            text = _scrub(_decode(output), secret)
            keep, drop_rest = _split_powershell_clixml(text)
            if drop_rest:
                self._drop_clixml = True
            if keep:
                on_output(keep)

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


def _is_pipe_broken(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        marker in lowered
        for marker in ("status_pipe_broken", "0xc000014b", "3221225803", "pipe has been closed")
    )


# Кириллица в выводе cmd.exe: OEM (cp866) или ANSI (cp1251), реже UTF-8.
_FALLBACK_ENCODINGS = ("cp866", "cp1251", "latin-1")


def _cyrillic_count(text: str) -> int:
    return sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")


def _decode(data) -> str:
    """Декодирует байты удалённого cmd/PsExec: UTF-8, иначе cp866/cp1251."""
    if data is None:
        return ""
    if not isinstance(data, bytes):
        return str(data)
    if not data:
        return ""

    utf8_text = data.decode("utf-8", errors="replace")
    if "\ufffd" not in utf8_text:
        return utf8_text

    best_text = utf8_text
    # Меньше replacement, больше кириллицы, при равенстве — раньше в fallback.
    best_key = (
        utf8_text.count("\ufffd"),
        -_cyrillic_count(utf8_text),
        len(_FALLBACK_ENCODINGS),
    )
    for index, encoding in enumerate(_FALLBACK_ENCODINGS):
        text = data.decode(encoding, errors="replace")
        key = (text.count("\ufffd"), -_cyrillic_count(text), index)
        if key < best_key:
            best_key = key
            best_text = text
    return best_text


# PowerShell 5.1 при неинтерактивном запуске (PsExec / EncodedCommand)
# дописывает в stderr сериализацию Information/Warning/Progress.
_CLIXML_MARKER = "#< CLIXML"
_CLIXML_ROOT = 'xmlns="http://schemas.microsoft.com/powershell'


def _split_powershell_clixml(text: str) -> tuple[str, bool]:
    """Отрезает CLIXML-дамп. True — дальше по каналу тоже XML, его отбрасываем."""
    if not text:
        return "", False
    idx = text.find(_CLIXML_MARKER)
    if idx == -1:
        idx = text.find("<Objs ")
        if idx == -1 or _CLIXML_ROOT not in text[idx : idx + 240]:
            return text, False
    return text[:idx].rstrip("\r\n"), True


def _strip_powershell_clixml(text: str) -> str:
    """Оставляет человекочитаемый вывод, без `#< CLIXML` и XML после него."""
    keep, _drop = _split_powershell_clixml(text)
    return keep


def _combine(stdout, stderr) -> str:
    out = _decode(stdout)
    err = _decode(stderr)
    if not err:
        merged = out
    elif not out:
        merged = err
    elif out.endswith("\n"):
        merged = out + err
    else:
        merged = out + "\n" + err
    return _strip_powershell_clixml(merged)


def _scrub(text: str, secret: str) -> str:
    if not text:
        return ""
    if secret:
        return text.replace(secret, "***")
    return text
