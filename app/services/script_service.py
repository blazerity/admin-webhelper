"""Библиотека скриптов и фоновый запуск ping / tracert / команды / скрипта.

Текст скрипта лежит либо в колонке scripts.content (storage=db),
либо в файле под SCRIPT_LIBRARY_DIR (storage=filesystem). В базу тогда
пишется только относительное имя файла. Абсолютный путь и «..»
отклоняются и при записи, и при чтении.

Запуск не блокирует HTTP-запрос. start_run фиксирует строку script_runs
со статусом pending и отдаёт работу демону. Демон умрёт при перезапуске
процесса, незавершённый запуск останется в running. Позже задача Celery
должна вызвать ту же execute_run с id запуска.

Пароль PsExec в command_text не пишется. Для скрипта в журнал кладётся
исходный текст библиотеки, не base64 для PowerShell.
"""

import logging
import threading
import uuid
from pathlib import Path

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Device, DeviceStatus, RunAs, RunStatus, RunType, Script, ScriptRun
from app.services.psexec_service import (
    RemoteExecError,
    cancel_remote,
    close_remote,
    discard_if_closed,
    run_remote_command,
    run_remote_script,
    show_close_button,
    was_cancelled,
)
from app.utils import clip, utcnow

logger = logging.getLogger(__name__)

_STOP_NOTE = "Выполнение остановлено."
_control_lock = threading.Lock()
_log_lock = threading.Lock()
# Событие отмены живёт, пока поток запуска не закончил работу.
_cancel_events: dict[int, threading.Event] = {}
_local_processes: dict[int, object] = {}

MAX_SCRIPT_CHARS = 100_000
_TARGET_OS = {"windows", "linux"}
_INTERPRETERS = {"powershell", "cmd", "bash"}
_STORAGES = {"db", "filesystem"}
_RUN_AS = {RunAs.PSEXEC, RunAs.SYSTEM}
_EXTENSIONS = {"powershell": ".ps1", "cmd": ".cmd", "bash": ".sh"}


class ScriptError(ValueError):
    """Ошибка библиотеки. Текст можно показать администратору во flash."""


def save_script(
    name: str,
    description: str,
    target_os: str,
    interpreter: str,
    storage: str,
    content: str,
    user_id: int | None,
    run_as: str = RunAs.PSEXEC,
    script: Script | None = None,
) -> Script:
    """Создаёт или обновляет скрипт. script=None — новая строка."""
    name = (name or "").strip()
    description = description or ""
    target_os = (target_os or "").strip().lower()
    interpreter = (interpreter or "").strip().lower()
    storage = (storage or "").strip().lower()
    run_as = (run_as or RunAs.PSEXEC).strip().lower()
    content = content if content is not None else ""

    if not name:
        raise ScriptError("Укажите название скрипта.")
    if len(name) > 128:
        raise ScriptError("Название длиннее 128 символов.")
    if target_os not in _TARGET_OS:
        raise ScriptError("ОС должна быть windows или linux.")
    if interpreter not in _INTERPRETERS:
        raise ScriptError("Интерпретатор: powershell, cmd или bash.")
    if storage not in _STORAGES:
        raise ScriptError("Хранилище: db или filesystem.")
    if run_as not in _RUN_AS:
        raise ScriptError("Запуск от имени: учётка PsExec или NT AUTHORITY\\SYSTEM.")
    if not str(content).strip():
        raise ScriptError("Текст скрипта пустой.")
    if len(content) > MAX_SCRIPT_CHARS:
        raise ScriptError(f"Текст скрипта длиннее {MAX_SCRIPT_CHARS} символов.")

    duplicate = Script.query.filter_by(name=name).one_or_none()
    if duplicate is not None and (script is None or duplicate.id != script.id):
        raise ScriptError("Скрипт с таким названием уже есть.")

    relative = None
    destination = None
    if storage == "filesystem":
        relative = _safe_filename(name, interpreter)
        taken = Script.query.filter_by(file_path=relative).one_or_none()
        if taken is not None and (script is None or taken.id != script.id):
            raise ScriptError("Файл с таким именем уже есть в библиотеке.")
        destination = _resolve_inside_library(relative)

    creating = script is None
    if creating:
        script = Script(created_by_id=user_id)
        db.session.add(script)

    old_file = script.file_path
    script.name = name
    script.description = description
    script.target_os = target_os
    script.interpreter = interpreter
    script.storage = storage
    script.run_as = run_as
    if creating:
        script.created_by_id = user_id

    if storage == "db":
        script.content = content
        script.file_path = None
    else:
        try:
            destination.write_text(content, encoding="utf-8")
        except OSError as exc:
            db.session.rollback()
            raise ScriptError("Не удалось записать файл скрипта.") from exc
        script.content = None
        script.file_path = relative

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ScriptError("Скрипт с таким названием уже есть.") from exc

    if storage == "db" and old_file:
        _delete_library_file(old_file)
    elif storage == "filesystem" and old_file and old_file != relative:
        _delete_library_file(old_file)
    return script


def delete_script(script_id: int) -> None:
    script = db.session.get(Script, script_id)
    if script is None:
        raise ScriptError("Скрипт не найден.")
    relative = script.file_path if script.storage == "filesystem" else None
    db.session.delete(script)
    db.session.commit()
    if relative:
        _delete_library_file(relative)


def get_script_body(script: Script) -> str:
    """Текст скрипта. Для файла путь ещё раз проверяется на выход из каталога."""
    if script.storage == "filesystem":
        path = _resolve_inside_library(script.file_path or "")
        if not path.is_file():
            raise ScriptError("Файл скрипта не найден в каталоге библиотеки.")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ScriptError("Не удалось прочитать файл скрипта.") from exc
    return script.content or ""


def start_run(
    run_type: str,
    user,
    device,
    command_text: str,
    script: Script | None = None,
    batch_id: str | None = None,
    run_as: str = RunAs.PSEXEC,
) -> ScriptRun:
    """Пишет pending-строку и запускает демон на execute_run.

    Демон-поток умрёт при перезапуске процесса. Позже задача Celery
    должна вызвать execute_run с тем же id запуска.
    """
    run = ScriptRun(
        script_id=script.id if script is not None else None,
        device_id=device.id if device is not None else None,
        user_id=user.id if user is not None else None,
        batch_id=batch_id,
        run_type=run_type,
        run_as=run_as if run_as in _RUN_AS else RunAs.PSEXEC,
        command_text=command_text or "",
        status=RunStatus.PENDING,
        log_text="",
        started_at=utcnow(),
    )
    db.session.add(run)
    db.session.commit()
    run_id = run.id
    # Прокси current_app в другом потоке пустой. Нужен настоящий объект приложения.
    app = current_app._get_current_object()
    threading.Thread(
        target=execute_run,
        args=(app, run_id),
        daemon=True,
        name=f"bawh-run-{run_id}",
    ).start()
    return run


def start_script_on_devices(script: Script, user, devices: list[Device]) -> list[ScriptRun]:
    """Один batch_id на пачку, отдельная строка и отдельный поток на устройство."""
    if not devices:
        return []
    batch_id = str(uuid.uuid4())
    # В журнал — исходный текст, не EncodedCommand.
    body = get_script_body(script)
    runs: list[ScriptRun] = []
    for device in devices:
        runs.append(
            start_run(
                RunType.SCRIPT,
                user,
                device,
                body,
                script=script,
                batch_id=batch_id,
                run_as=script.run_as or RunAs.PSEXEC,
            )
        )
    return runs


def cancel_run(run_id: int) -> None:
    """Останавливает живой запуск. Если потока уже нет — помечает строку."""
    with _control_lock:
        event = _cancel_events.get(run_id)
        process = _local_processes.get(run_id)
        if event is not None:
            event.set()
    if process is not None and getattr(process, "poll", lambda: 0)() is None:
        try:
            process.terminate()
        except OSError:
            logger.warning("Не удалось остановить локальный процесс запуска %s", run_id)
    had_remote = cancel_remote(run_id)
    if event is None and not had_remote:
        with _control_lock:
            if run_id in _cancel_events:
                _cancel_events[run_id].set()
                return
        _force_cancel_row(run_id)


def close_run_session(run_id: int) -> bool:
    """Закрывает SMB-сессию, если она осталась после запуска."""
    return close_remote(run_id)


def run_session_open(run_id: int, finished: bool) -> bool:
    return show_close_button(run_id, finished)


def execute_run(app, run_id: int) -> None:
    """Выполняет уже созданную строку script_runs. Её же позовёт Celery."""
    with app.app_context():
        cancel_event = threading.Event()
        try:
            with _control_lock:
                run = db.session.get(ScriptRun, run_id)
                if run is None:
                    logger.warning("Запуск %s не найден.", run_id)
                    return
                if run.status == RunStatus.CANCELLED:
                    return
                _cancel_events[run_id] = cancel_event
                run.status = RunStatus.RUNNING
                db.session.commit()

            run = db.session.get(ScriptRun, run_id)

            def on_output(text: str) -> None:
                _append_log(app, run_id, text)

            def on_process(process) -> None:
                with _control_lock:
                    _local_processes[run_id] = process

            exit_code, log_text, status = _perform(
                run.run_type,
                run.device.ip if run.device is not None else "",
                run.command_text or "",
                run.script,
                run.run_as or RunAs.PSEXEC,
                run.user_id,
                on_output=on_output,
                cancel_event=cancel_event,
                on_process=on_process,
                run_id=run_id,
            )
            if cancel_event.is_set() or was_cancelled(run_id):
                status = RunStatus.CANCELLED
                log_text = _with_stop_note(log_text)
            limit = int(app.config.get("MAX_LOG_CHARS", 200_000))
            run = db.session.get(ScriptRun, run_id)
            if run.status == RunStatus.CANCELLED and status != RunStatus.CANCELLED:
                return
            run.log_text = clip(log_text, limit)
            run.exit_code = exit_code
            run.status = status
            run.finished_at = utcnow()
            db.session.commit()
        except Exception as exc:
            # Сообщение уже без пароля: psexec_service вычищает его до исключения.
            logger.exception("Запуск %s завершился ошибкой", run_id)
            _mark_failed(app, run_id, exc, cancel_event)
        finally:
            with _control_lock:
                _cancel_events.pop(run_id, None)
                _local_processes.pop(run_id, None)
            discard_if_closed(run_id)
            db.session.remove()


def _perform(
    run_type: str,
    ip: str,
    command_text: str,
    script: Script | None,
    run_as: str = RunAs.PSEXEC,
    user_id: int | None = None,
    on_output=None,
    cancel_event: threading.Event | None = None,
    on_process=None,
    run_id: int | None = None,
):
    if not ip:
        raise ScriptError("У запуска нет IP устройства.")
    if run_type == RunType.PING:
        # Импорт внутри воркера: тест подменяет app.services.ping_service.ping_host.
        from app.services.ping_service import ping_host

        result = ping_host(
            ip,
            on_output=on_output,
            cancel_event=cancel_event,
            on_process=on_process,
        )
        online = result.status == DeviceStatus.ONLINE
        return (
            0 if online else 1,
            result.output or "",
            RunStatus.SUCCESS if online else RunStatus.FAILED,
        )
    if run_type == RunType.TRACERT:
        from app.services.ping_service import trace_host

        rc, output = trace_host(
            ip,
            on_output=on_output,
            cancel_event=cancel_event,
            on_process=on_process,
        )
        return rc, output or "", RunStatus.SUCCESS if rc == 0 else RunStatus.FAILED
    if run_type == RunType.COMMAND:
        rc, output = run_remote_command(
            ip,
            command_text,
            user_id=user_id,
            on_output=on_output,
            run_id=run_id,
        )
        return rc, output, RunStatus.SUCCESS if rc == 0 else RunStatus.FAILED
    if run_type == RunType.SCRIPT:
        if script is None:
            raise ScriptError("Скрипт для этого запуска уже удалён.")
        if script.target_os == "linux" or script.interpreter == "bash":
            raise RemoteExecError(
                "Удалённый Linux в v1 не реализован: PsExec работает только с Windows."
            )
        body = get_script_body(script)
        rc, output = run_remote_script(
            ip,
            script.interpreter,
            body,
            as_system=run_as == RunAs.SYSTEM,
            user_id=user_id,
            on_output=on_output,
            run_id=run_id,
        )
        return rc, output, RunStatus.SUCCESS if rc == 0 else RunStatus.FAILED
    raise ScriptError(f"Неизвестный тип запуска: {run_type}")


def _append_log(app, run_id: int, text: str) -> None:
    """Дописывает кусок вывода, не дожидаясь конца процесса."""
    if not text:
        return
    with _log_lock:
        with app.app_context():
            try:
                run = db.session.get(ScriptRun, run_id)
                if run is None or run.status in RunStatus.FINISHED:
                    return
                limit = int(app.config.get("MAX_LOG_CHARS", 200_000))
                run.log_text = clip((run.log_text or "") + text, limit)
                db.session.commit()
            except Exception:
                logger.exception("Не удалось дописать лог запуска %s", run_id)
                db.session.rollback()
            finally:
                # Канал PsExec пишет из своего потока. Своя сессия ему больше не нужна.
                # В потоке запуска сессия тоже заново откроется на следующей строке.
                db.session.remove()


def _with_stop_note(log_text: str) -> str:
    text = log_text or ""
    if _STOP_NOTE in text:
        return text
    if not text:
        return _STOP_NOTE
    if text.endswith("\n"):
        return text + _STOP_NOTE
    return text + "\n" + _STOP_NOTE


def _force_cancel_row(run_id: int) -> None:
    run = db.session.get(ScriptRun, run_id)
    if run is None or run.status in RunStatus.FINISHED:
        return
    limit = int(current_app.config.get("MAX_LOG_CHARS", 200_000))
    run.status = RunStatus.CANCELLED
    run.finished_at = utcnow()
    run.log_text = clip(_with_stop_note(run.log_text or ""), limit)
    db.session.commit()


def _mark_failed(app, run_id: int, exc: BaseException, cancel_event: threading.Event) -> None:
    try:
        db.session.rollback()
        run = db.session.get(ScriptRun, run_id)
        if run is None:
            return
        limit = int(app.config.get("MAX_LOG_CHARS", 200_000))
        stopped = cancel_event.is_set() or was_cancelled(run_id)
        previous = run.log_text or ""
        if stopped:
            run.status = RunStatus.CANCELLED
            merged = _with_stop_note(previous)
        else:
            run.status = RunStatus.FAILED
            piece = str(exc)
            merged = previous
            if piece and piece not in merged:
                merged = f"{merged}\n{piece}" if merged else piece
        run.exit_code = None
        run.finished_at = utcnow()
        run.log_text = clip(merged, limit)
        db.session.commit()
    except Exception:
        logger.exception("Не удалось записать ошибку запуска %s", run_id)
        db.session.rollback()


def library_root() -> Path:
    """Каталог библиотеки. Относительный путь считается от корня проекта."""
    configured = current_app.config.get("SCRIPT_LIBRARY_DIR") or "script_library"
    path = Path(configured)
    if not path.is_absolute():
        path = Path(current_app.root_path).parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _safe_filename(name: str, interpreter: str) -> str:
    """Имя файла из названия: буквы, цифры, дефис и подчёркивание.

    «..», слэш и двоеточие отклоняются до подмены символов, иначе
    ../../outside превратилось бы в безобидное outside и ошибка спряталась.
    """
    if any(ch in name for ch in "/\\:") or ".." in name:
        raise ScriptError(
            "Название не должно быть путём: файл остаётся внутри каталога библиотеки."
        )
    stem = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in name).strip("._")
    if not stem or stem in {".", ".."} or ".." in stem:
        raise ScriptError("Из названия не получается безопасное имя файла.")
    return stem + _EXTENSIONS[interpreter]


def _resolve_inside_library(relative: str) -> Path:
    raw = (relative or "").strip()
    path = Path(raw)
    if (
        not raw
        or path.is_absolute()
        or len(path.parts) != 1
        or path.parts[0] in {".", ".."}
    ):
        raise ScriptError("Путь к файлу скрипта выходит за каталог библиотеки.")
    root = library_root()
    candidate = (root / path.parts[0]).resolve()
    if not candidate.is_relative_to(root):
        raise ScriptError("Путь к файлу скрипта выходит за каталог библиотеки.")
    return candidate


def _delete_library_file(relative: str) -> None:
    try:
        path = _resolve_inside_library(relative)
    except ScriptError:
        logger.warning("Пропускаю удаление файла вне библиотеки.")
        return
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        logger.warning("Не удалось удалить файл скрипта %s", path.name)
