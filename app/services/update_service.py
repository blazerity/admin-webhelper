"""Обновление кода из публичного git и откат на сохранённую копию.

Кнопка в интерфейсе администратора вызывает begin_update / begin_rollback.
Сначала на диск пишется полная копия текущего кода. Пока копия не создана,
файлы приложения не меняются. Потом репозиторий скачивается во временный
каталог и рабочие файлы заменяются.

Номер релиза берётся из файла VERSION (semver). Сообщения и страница
обновлений показывают его; коммит сравнивается внутри, чтобы понять,
есть ли новая сборка.

Не копируются и не перезаписываются: .env, .venv, logs, script_library,
instance, backups, базы *.db. Это данные сервера, а не версия программы.

При откате кода схема PostgreSQL не откатывается назад.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from flask import current_app

from app.utils import clip, utcnow
from app.version import parse_version, read_version_file

logger = logging.getLogger(__name__)

_BACKUP_ID = re.compile(r"^[0-9]{8}-[0-9]{6,12}-[a-z0-9]{4,40}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,80}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_HTTPS = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "logs",
    "script_library",
    "backups",
    "instance",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
}
_REASON_LABELS = {
    "before-update": "перед обновлением",
    "before-rollback": "перед откатом",
}
_LOCK_MAX_AGE_SECONDS = 30 * 60


class UpdateError(Exception):
    """Ошибка обновления. Текст можно показать администратору."""


@dataclass(frozen=True)
class UpdateResult:
    changed: bool
    message: str
    backup_id: str | None
    commit: str
    version: str = ""


@dataclass(frozen=True)
class BackupInfo:
    id: str
    created_at: str
    commit: str
    subject: str
    reason_label: str
    version: str = ""


@dataclass(frozen=True)
class UpdatePage:
    root: str
    git_available: bool
    remote_url: str
    remote_error: str
    branch: str
    local_commit: str
    local_subject: str
    local_version: str
    remote_commit: str
    remote_subject: str
    remote_version: str
    remote_checked_at: str
    backups: list[BackupInfo]
    operation_state: str
    operation_message: str

    @property
    def update_available(self) -> bool | None:
        if not self.remote_commit:
            return None
        if not self.local_commit:
            return True
        return self.local_commit != self.remote_commit

    @property
    def running(self) -> bool:
        return self.operation_state == "running"


def validate_remote_url(url: str) -> str:
    """Публичный HTTPS без логина и пароля в адресе."""
    raw = (url or "").strip()
    if not raw or any(char in raw for char in ("\n", "\r", "\x00", " ", "\t")):
        raise UpdateError("Укажите публичный HTTPS-адрес репозитория в GIT_REMOTE_URL.")
    if len(raw) > 500:
        raise UpdateError("Адрес репозитория слишком длинный.")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise UpdateError("Разрешён только публичный адрес, который начинается с https://.")
    if parsed.username or parsed.password:
        raise UpdateError("В адресе репозитория не должно быть логина и пароля.")
    return raw


def validate_branch(branch: str) -> str:
    raw = (branch or "").strip()
    if (
        not _BRANCH.fullmatch(raw)
        or raw.startswith("-")
        or raw.startswith("/")
        or raw.endswith("/")
        or ".." in raw
        or "//" in raw
    ):
        raise UpdateError("Некорректное имя ветки в GIT_BRANCH.")
    return raw


def project_root() -> Path:
    return Path(current_app.config["PROJECT_ROOT"]).resolve()


def backup_dir(root: Path) -> Path:
    return (root / "backups").resolve()


def build_page() -> UpdatePage:
    """Состояние для страницы. Сеть не трогает: проверка — отдельная кнопка."""
    root = project_root()
    configured = str(current_app.config.get("GIT_REMOTE_URL") or "")
    branch = str(current_app.config.get("GIT_BRANCH") or "main")
    remote_url = ""
    remote_error = ""
    try:
        branch = validate_branch(branch)
        remote_url = resolve_remote_url(root, configured)
    except UpdateError as exc:
        remote_error = str(exc)
    local_commit, local_subject, local_ver = local_version(root)
    remote = _read_json(backup_dir(root) / "remote-check.json")
    operation = _current_operation(root)
    return UpdatePage(
        root=str(root),
        git_available=shutil.which("git") is not None,
        remote_url=remote_url,
        remote_error=remote_error,
        branch=branch,
        local_commit=local_commit,
        local_subject=local_subject,
        local_version=local_ver,
        remote_commit=str(remote.get("commit") or ""),
        remote_subject=str(remote.get("subject") or ""),
        remote_version=parse_version(str(remote.get("version") or "")),
        remote_checked_at=str(remote.get("checked_at") or ""),
        backups=list_backups(root),
        operation_state=str(operation.get("state") or ""),
        operation_message=str(operation.get("message") or ""),
    )


def check_for_updates() -> str:
    """Спрашивает удалённую ветку и запоминает версию/коммит для страницы."""
    root = project_root()
    if shutil.which("git") is None:
        raise UpdateError("На сервере не найдена команда git.")
    url = resolve_remote_url(root, str(current_app.config.get("GIT_REMOTE_URL") or ""))
    branch = validate_branch(str(current_app.config.get("GIT_BRANCH") or "main"))
    remote_commit = remote_head(url, branch, cwd=root)
    remote_ver = remote_version(url, branch, remote_commit, cwd=root)
    _write_json(
        backup_dir(root) / "remote-check.json",
        {
            "commit": remote_commit,
            "version": remote_ver,
            "subject": "",
            "checked_at": _now_label(),
        },
    )
    local_commit, _subject, local_ver = local_version(root)
    if local_commit == remote_commit:
        return f"Обновлений нет. Установлена версия {_display_version(local_ver, remote_commit)}."
    if not local_commit:
        return (
            f"В репозитории версия {_display_version(remote_ver, remote_commit)}. "
            "Локальная установка ещё не записывалась — можно обновить."
        )
    return (
        f"Доступно обновление: {_display_version(local_ver, local_commit)} → "
        f"{_display_version(remote_ver, remote_commit)}."
    )


def begin_update() -> None:
    """Ставит обновление в фон, чтобы запрос не упирался в таймаут Gunicorn."""
    root = project_root()
    url = resolve_remote_url(root, str(current_app.config.get("GIT_REMOTE_URL") or ""))
    branch = validate_branch(str(current_app.config.get("GIT_BRANCH") or "main"))
    keep = _keep_count()
    restart = bool(current_app.config.get("UPDATE_RESTART"))
    if shutil.which("git") is None:
        raise UpdateError("На сервере не найдена команда git.")
    lock = _Lock(backup_dir(root) / ".lock")
    if not lock.acquire():
        raise UpdateError("Обновление или откат уже выполняются.")
    _write_operation(root, "running", "Начинаю обновление. Сначала будет создана резервная копия.")

    def job() -> None:
        try:
            result = perform_update(
                root=root,
                remote_url=url,
                branch=branch,
                keep=keep,
                on_progress=lambda message: _write_operation(root, "running", message),
            )
            message = result.message
            if result.changed and restart:
                message = f"{message} {schedule_restart(root, result.message)}".strip()
            _write_operation(root, "success", message, result.backup_id)
        except UpdateError as exc:
            logger.warning("update failed: %s", exc)
            _write_operation(root, "error", str(exc))
        except Exception:
            logger.exception("update failed")
            _write_operation(root, "error", "Обновление не выполнено. Подробности в журнале.")
        finally:
            lock.release()

    threading.Thread(target=job, name="bawh-update", daemon=True).start()


def begin_rollback(backup_id: str) -> None:
    root = project_root()
    # Проверяем id до фоновой работы, чтобы опечатка сразу вернулась в форму.
    backup_path(root, backup_id)
    keep = _keep_count()
    restart = bool(current_app.config.get("UPDATE_RESTART"))
    branch = validate_branch(str(current_app.config.get("GIT_BRANCH") or "main"))
    lock = _Lock(backup_dir(root) / ".lock")
    if not lock.acquire():
        raise UpdateError("Обновление или откат уже выполняются.")
    _write_operation(root, "running", "Начинаю откат. Текущая версия тоже будет сохранена.")

    def job() -> None:
        try:
            result = perform_rollback(
                root=root,
                backup_id=backup_id,
                branch=branch,
                keep=keep,
                on_progress=lambda message: _write_operation(root, "running", message),
            )
            message = result.message
            if result.changed and restart:
                message = f"{message} {schedule_restart(root, result.message)}".strip()
            _write_operation(root, "success", message, result.backup_id)
        except UpdateError as exc:
            logger.warning("rollback failed: %s", exc)
            _write_operation(root, "error", str(exc))
        except Exception:
            logger.exception("rollback failed")
            _write_operation(root, "error", "Откат не выполнен. Подробности в журнале.")
        finally:
            lock.release()

    threading.Thread(target=job, name="bawh-rollback", daemon=True).start()


def perform_update(
    *,
    root: Path,
    remote_url: str,
    branch: str,
    keep: int,
    on_progress=None,
) -> UpdateResult:
    """Скачать ветку и заменить код. Без готовой копии замена не начинается."""
    url = validate_remote_url(remote_url)
    branch = validate_branch(branch)
    progress = on_progress or (lambda _message: None)
    progress("Проверяю версию в репозитории")
    remote_commit = remote_head(url, branch, cwd=root)
    local_commit, _subject, local_ver = local_version(root)
    if local_commit and local_commit == remote_commit:
        return UpdateResult(
            False,
            f"Уже установлена последняя версия {_display_version(local_ver, remote_commit)}.",
            None,
            remote_commit,
            local_ver,
        )

    progress("Создаю резервную копию текущей версии")
    try:
        backup = create_backup(root, reason="before-update")
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Резервная копия не создана, файлы не менялись: {exc}") from exc

    clone_dir = backup_dir(root) / ".clone-tmp"
    step = "download"
    old_req = _read_bytes(root / "requirements.txt")
    new_req = old_req
    try:
        progress("Скачиваю репозиторий")
        commit, subject = clone_repository(url, branch, clone_dir)
        if commit != remote_commit:
            raise UpdateError("Скачанный коммит не совпал с коммитом на сервере. Замена отменена.")
        version = read_version_file(clone_dir)
        new_req = _read_bytes(clone_dir / "requirements.txt")
        step = "replace"
        progress("Заменяю файлы приложения")
        sync_managed(clone_dir, root)
        _clear_pycache(root)
        if old_req != new_req:
            progress("Устанавливаю зависимости")
            pip_install(root)
        progress("Готовлю схему базы данных")
        prepare_schema(root)
        align_git_head(root, url, branch)
        _write_installed(root, commit, subject, version, branch, backup.name)
        prune_backups(root, keep, protect={backup.name})
        logger.info("updated to %s (%s), backup %s", version or commit[:12], commit[:12], backup.name)
        return UpdateResult(
            True,
            (
                f"Установлена версия {_display_version(version, commit)}"
                f"{f' ({subject})' if subject else ''}. "
                f"Предыдущая версия сохранена в копии {backup.name}."
            ),
            backup.name,
            commit,
            version,
        )
    except Exception as exc:
        if step == "download":
            _rmtree(backup)
            detail = str(exc) if isinstance(exc, UpdateError) else f"Репозиторий не скачан: {exc}"
            raise UpdateError(f"{_sentence(detail)} Файлы приложения не менялись.") from exc
        restore_note = _restore_after_failure(root, backup, old_req, new_req)
        detail = str(exc) if isinstance(exc, UpdateError) else f"Обновление не выполнено: {exc}"
        raise UpdateError(f"{_sentence(detail)} {restore_note}") from exc
    finally:
        _rmtree(clone_dir)


def perform_rollback(
    *,
    root: Path,
    backup_id: str,
    branch: str,
    keep: int,
    on_progress=None,
) -> UpdateResult:
    """Вернуть файлы из копии. Текущий код перед этим тоже сохраняется."""
    selected = backup_path(root, backup_id)
    branch = validate_branch(branch)
    progress = on_progress or (lambda _message: None)
    manifest = _read_manifest(selected)
    progress("Создаю копию текущей версии перед откатом")
    try:
        safety = create_backup(root, reason="before-rollback")
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Копия текущей версии не создана, откат отменён: {exc}") from exc

    old_req = _read_bytes(root / "requirements.txt")
    restored_req = old_req
    try:
        progress(f"Восстанавливаю копию {selected.name}")
        commit = str(manifest.get("commit") or "")
        version = parse_version(str(manifest.get("version") or "")) or read_version_file(selected / "tree")
        restore_tree(root, selected)
        _point_git_commit(root, commit)
        _clear_pycache(root)
        restored_req = _read_bytes(root / "requirements.txt")
        if old_req != restored_req:
            progress("Ставлю зависимости выбранной версии")
            pip_install(root)
        subject = str(manifest.get("subject") or "")
        if not version:
            version = read_version_file(root)
        _write_installed(root, commit, subject, version, branch, selected.name)
        prune_backups(root, keep, protect={selected.name, safety.name})
        label = _display_version(version, commit if commit and commit != "none" else "")
        logger.info("rolled back to backup %s (%s)", selected.name, label)
        return UpdateResult(
            True,
            (
                f"Восстановлена копия {selected.name} (версия {label}). "
                f"Версия, которая была до отката, сохранена в {safety.name}. "
                "Схема базы назад не откатывается."
            ),
            safety.name,
            commit,
            version,
        )
    except Exception as exc:
        restore_note = _restore_after_failure(root, safety, old_req, restored_req)
        detail = str(exc) if isinstance(exc, UpdateError) else f"Откат не выполнен: {exc}"
        raise UpdateError(f"{_sentence(detail)} {restore_note}") from exc


def create_backup(root: Path, *, reason: str) -> Path:
    if reason not in _REASON_LABELS:
        raise UpdateError("Неизвестная причина копии.")
    root = root.resolve()
    commit, subject, version = local_version(root)
    short = commit[:12] if _COMMIT.fullmatch(commit or "") else "none"
    now = utcnow()
    backup_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{short}"
    dest = backup_dir(root) / backup_id
    if dest.exists():
        backup_id = f"{now.strftime('%Y%m%d-%H%M%S%f')}-{short}"
        dest = backup_dir(root) / backup_id
    tree = dest / "tree"
    try:
        tree.mkdir(parents=True, exist_ok=False)
        for rel in iter_managed(root):
            target = tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, target)
        _write_json(
            dest / "manifest.json",
            {
                "id": backup_id,
                "created_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "commit": commit,
                "subject": subject,
                "version": version,
                "reason": reason,
            },
        )
    except Exception:
        _rmtree(dest)
        raise
    logger.info("backup %s created (%s)", backup_id, reason)
    return dest


def list_backups(root: Path) -> list[BackupInfo]:
    base = backup_dir(root)
    if not base.is_dir():
        return []
    found: list[BackupInfo] = []
    for path in base.iterdir():
        if not path.is_dir() or not _BACKUP_ID.fullmatch(path.name):
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = _read_json(manifest_path)
        reason = str(manifest.get("reason") or "")
        version = parse_version(str(manifest.get("version") or ""))
        if not version:
            version = read_version_file(path / "tree")
        found.append(
            BackupInfo(
                id=path.name,
                created_at=str(manifest.get("created_at") or ""),
                commit=str(manifest.get("commit") or ""),
                subject=str(manifest.get("subject") or ""),
                reason_label=_REASON_LABELS.get(reason, reason or "копия"),
                version=version,
            )
        )
    found.sort(key=lambda item: item.id, reverse=True)
    return found


def backup_path(root: Path, backup_id: str) -> Path:
    if not _BACKUP_ID.fullmatch(backup_id or ""):
        raise UpdateError("Некорректный идентификатор копии.")
    base = backup_dir(root)
    path = (base / backup_id).resolve()
    if path.parent != base.resolve():
        raise UpdateError("Некорректный идентификатор копии.")
    if not (path / "manifest.json").is_file() or not (path / "tree").is_dir():
        raise UpdateError("Такая копия не найдена.")
    return path


def resolve_remote_url(root: Path, configured: str) -> str:
    if (configured or "").strip():
        return validate_remote_url(configured)
    origin = _origin_url(root)
    if not origin:
        raise UpdateError("Укажите публичный HTTPS-адрес репозитория в GIT_REMOTE_URL.")
    return validate_remote_url(origin)


def local_version(root: Path) -> tuple[str, str, str]:
    """Локальный коммит, тема последнего коммита и номер версии из VERSION."""
    installed = _read_json(backup_dir(root) / "installed.json")
    commit = str(installed.get("commit") or "")
    subject = str(installed.get("subject") or "")
    version = parse_version(str(installed.get("version") or "")) or read_version_file(root)
    if commit:
        return commit, subject, version
    git_dir = root / ".git"
    if git_dir.exists() and shutil.which("git"):
        try:
            commit = _git_text("rev-parse", "HEAD", cwd=root)
            subject = _git_text("log", "-1", "--format=%s", cwd=root)
            return commit, subject, version or read_version_file(root)
        except UpdateError:
            return "", "", version
    return "", "", version


def remote_version(url: str, branch: str, commit: str, *, cwd: Path) -> str:
    """Прочитать VERSION с удалённого коммита (GitHub raw или короткий clone)."""
    if not _COMMIT.fullmatch(commit or ""):
        return ""
    github = _GITHUB_HTTPS.match((url or "").rstrip("/"))
    if github:
        owner = github.group("owner")
        repo = github.group("repo")
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/VERSION"
        try:
            from urllib.error import HTTPError, URLError
            from urllib.request import Request, urlopen

            request = Request(raw_url, headers={"User-Agent": "bAWH-update-check"})
            with urlopen(request, timeout=20) as response:  # noqa: S310 — публичный raw GitHub
                body = response.read(256).decode("utf-8", errors="replace")
            parsed = parse_version(body)
            if parsed:
                return parsed
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            logger.info("remote VERSION via GitHub raw unavailable: %s", exc)

    peek = backup_dir(cwd) / ".version-peek"
    try:
        cloned_commit, _subject = clone_repository(url, branch, peek)
        if cloned_commit != commit:
            logger.info("remote VERSION peek commit mismatch: %s != %s", cloned_commit[:12], commit[:12])
        return read_version_file(peek)
    except UpdateError as exc:
        logger.info("remote VERSION peek failed: %s", exc)
        return ""
    finally:
        _rmtree(peek)


def remote_head(url: str, branch: str, *, cwd: Path) -> str:
    result = _run(
        _git("ls-remote", url, f"refs/heads/{branch}"),
        cwd=cwd,
        timeout=60,
    )
    if result.returncode != 0:
        raise UpdateError(f"Не удалось обратиться к репозиторию: {_tail(result.stderr)}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise UpdateError(f"В репозитории нет ветки {branch}.")
    sha = lines[0].split()[0].strip().lower()
    if not _COMMIT.fullmatch(sha):
        raise UpdateError("Неожиданный ответ git ls-remote.")
    return sha


def clone_repository(url: str, branch: str, dest: Path) -> tuple[str, str]:
    _rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        _git(
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            "--single-branch",
            url,
            str(dest),
        ),
        cwd=dest.parent,
        timeout=180,
    )
    if result.returncode != 0 or not dest.is_dir():
        _rmtree(dest)
        raise UpdateError(f"Не удалось скачать репозиторий: {_tail(result.stderr)}")
    commit = _git_text("rev-parse", "HEAD", cwd=dest)
    subject = clip(_git_text("log", "-1", "--format=%s", cwd=dest), 200)
    if not _COMMIT.fullmatch(commit):
        raise UpdateError("В скачанном репозитории нет коммита.")
    return commit, subject


def pip_install(root: Path) -> None:
    requirements = root / "requirements.txt"
    if not requirements.is_file():
        return
    result = _run(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
        cwd=root,
        timeout=300,
    )
    if result.returncode != 0:
        raise UpdateError(f"Не удалось установить зависимости: {_tail(result.stderr)}")


def prepare_schema(root: Path) -> None:
    result = _run(
        [sys.executable, "-m", "flask", "--app", "wsgi", "init-db"],
        cwd=root,
        timeout=180,
    )
    if result.returncode != 0:
        detail = _tail(result.stderr or result.stdout)
        raise UpdateError(f"Не удалось подготовить схему базы: {detail}")


def align_git_head(root: Path, url: str, branch: str) -> None:
    """Поставить клон установщика на скачанный коммит, чтобы повторный install-debian12.sh смог сделать pull --ff-only.

    Без --depth: иначе каталог становится shallow, а reset --mixed оставляет
    рабочие файлы «изменёнными» и следующий git pull --ff-only в установщике
    останавливается. reset --hard совпадает с только что скопированным деревом.
    Файлы вне git (.env, .venv, logs, backups) он не удаляет.
    """
    if not (root / ".git").exists():
        return
    try:
        fetched = _run(_git("fetch", url, branch), cwd=root, timeout=120)
        if fetched.returncode != 0:
            logger.warning("git fetch after update failed: %s", _tail(fetched.stderr))
            return
        moved = _run(_git("reset", "--hard", "FETCH_HEAD"), cwd=root, timeout=60)
        if moved.returncode != 0:
            logger.warning("git reset after update failed: %s", _tail(moved.stderr))
    except UpdateError as exc:
        logger.warning("git head was not moved: %s", exc)


def schedule_restart(root: Path, previous: str) -> str:
    """Перезапуск служб после ответа странице. На Windows службы systemd нет."""
    from app.services.systemd_service import (
        resolve_sudo_credentials,
        run_service_restart,
        systemctl_path,
    )

    systemctl = systemctl_path()
    if systemctl is None:
        return "Перезапустите процесс приложения, чтобы подхватить новую версию."

    sudo_user, sudo_password = resolve_sudo_credentials()

    def _later() -> None:
        time.sleep(2)
        result = run_service_restart(systemctl, sudo_user, sudo_password)
        if result.returncode != 0:
            hint = "sudo systemctl restart bawh-scheduler bawh-web"
            if sudo_user:
                hint = f"su - {sudo_user} -c '{systemctl} restart bawh-scheduler bawh-web'"
            detail = _tail(result.stderr or result.stdout)
            _write_operation(
                root,
                "success",
                previous
                + " Службы сами не перезапустились. "
                + f"Выполните: {hint}. "
                + "Проверьте sudo-учётку в Параметры → Управление службами "
                + "(логин/пароль Linux или правило deploy/bawh-update.sudoers). "
                + detail,
            )

    threading.Thread(target=_later, name="bawh-restart", daemon=True).start()
    return "Службы bawh-scheduler и bawh-web будут перезапущены через несколько секунд."


def restart_command(systemctl: str, sudo_user: str = "", *, with_password: bool = False) -> list[str]:
    """Команда перезапуска служб — делегат в systemd_service."""
    from app.services.systemd_service import restart_command as _restart_command

    return _restart_command(systemctl, sudo_user, with_password=with_password)


def run_service_restart(
    systemctl: str,
    sudo_user: str = "",
    sudo_password: str = "",
) -> subprocess.CompletedProcess:
    """Перезапускает службы: с паролем через su -P, иначе sudo -n."""
    from app.services.systemd_service import run_service_restart as _run

    return _run(systemctl, sudo_user, sudo_password)


def iter_managed(root: Path):
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            path = current / name
            if name in _SKIP_DIRS or path.is_symlink():
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name == ".env" or name.endswith(".db"):
                continue
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path.relative_to(root)


def sync_managed(src: Path, dst: Path) -> None:
    """Скопировать рабочие файлы src поверх dst и убрать те, которых в src уже нет."""
    if not src.is_dir():
        raise UpdateError("Нет каталога со скачанной версией, файлы не заменялись.")
    src_rels = set(iter_managed(src))
    dst_rels = set(iter_managed(dst))
    for rel in src_rels:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)
    for rel in dst_rels - src_rels:
        (dst / rel).unlink(missing_ok=True)
    _remove_empty_dirs(dst)


def restore_tree(root: Path, backup: Path) -> None:
    tree = backup / "tree"
    if not tree.is_dir():
        raise UpdateError(f"В копии {backup.name} нет файлов.")
    sync_managed(tree, root)


def prune_backups(root: Path, keep: int, protect: set[str]) -> None:
    keep = max(1, int(keep))
    items = list_backups(root)
    for index, item in enumerate(items):
        if index < keep or item.id in protect:
            continue
        _rmtree(backup_dir(root) / item.id)


def _restore_after_failure(root: Path, backup: Path, old_req: bytes, new_req: bytes) -> str:
    try:
        restore_tree(root, backup)
        _clear_pycache(root)
    except Exception as exc:
        return (
            f"Вернуть предыдущую версию не удалось ({exc}). "
            f"Копия осталась в {backup.name}."
        )
    dep_note = ""
    if old_req != new_req:
        try:
            pip_install(root)
        except UpdateError as exc:
            dep_note = f" Повторно поставить прежние зависимости не удалось: {exc}."
    return f"Предыдущая версия восстановлена из копии {backup.name}.{dep_note}"


def _point_git_commit(root: Path, commit: str) -> None:
    """Поставить текущую ветку на коммит копии, не переписывая восстановленные файлы."""
    if not _COMMIT.fullmatch(commit or "") or not (root / ".git").exists():
        return
    probe = _run(_git("cat-file", "-e", f"{commit}^{{commit}}"), cwd=root, timeout=30)
    if probe.returncode != 0:
        return
    moved = _run(_git("reset", "--mixed", commit), cwd=root, timeout=60)
    if moved.returncode != 0:
        logger.warning("git reset after rollback failed: %s", _tail(moved.stderr))


def _origin_url(root: Path) -> str:
    if not (root / ".git").exists() or shutil.which("git") is None:
        return ""
    result = _run(_git("remote", "get-url", "origin"), cwd=root, timeout=20)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _write_installed(
    root: Path,
    commit: str,
    subject: str,
    version: str,
    branch: str,
    backup_id: str,
) -> None:
    _write_json(
        backup_dir(root) / "installed.json",
        {
            "commit": commit,
            "subject": subject,
            "version": parse_version(version) or read_version_file(root),
            "branch": branch,
            "backup_id": backup_id,
            "installed_at": _now_label(),
        },
    )


def _display_version(version: str, commit: str = "") -> str:
    """Человекочитаемая метка: номер версии, иначе короткий коммит."""
    cleaned = parse_version(version)
    if cleaned:
        return cleaned
    if commit and _COMMIT.fullmatch(commit):
        return f"сборка {commit[:12]}"
    return "неизвестная"


def _read_manifest(backup: Path) -> dict:
    manifest = _read_json(backup / "manifest.json")
    if not manifest:
        raise UpdateError(f"У копии {backup.name} нет описания.")
    return manifest


def _current_operation(root: Path) -> dict:
    path = backup_dir(root) / "operation.json"
    data = _read_json(path)
    if data.get("state") == "running" and not _lock_active(backup_dir(root) / ".lock"):
        data = {
            "state": "error",
            "message": "Обновление прервалось. Если код сменился не до конца, откатитесь на последнюю копию.",
        }
        _write_json(path, data)
    return data


def _write_operation(root: Path, state: str, message: str, backup_id: str | None = None) -> None:
    payload = {"state": state, "message": message}
    if backup_id:
        payload["backup_id"] = backup_id
    _write_json(backup_dir(root) / "operation.json", payload)


def _keep_count() -> int:
    try:
        return max(1, int(current_app.config.get("UPDATE_BACKUP_KEEP", 5)))
    except (TypeError, ValueError):
        return 5


def _git(*args: str) -> list[str]:
    return ["git", "-c", "safe.directory=*", *args]


def _git_text(*args: str, cwd: Path) -> str:
    result = _run(_git(*args), cwd=cwd, timeout=30)
    if result.returncode != 0:
        raise UpdateError(_tail(result.stderr or result.stdout))
    return result.stdout.strip()


def _run(args: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        return subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("Команда превысила время ожидания.") from exc
    except FileNotFoundError as exc:
        raise UpdateError(f"Не найдена команда {args[0]}.") from exc


def _clear_pycache(root: Path) -> None:
    for dirpath, dirnames, _filenames in os.walk(root, topdown=True):
        current = Path(dirpath)
        if current.name in {".venv", "venv", "backups", "script_library", "logs"}:
            dirnames.clear()
            continue
        if "__pycache__" in dirnames:
            _rmtree(current / "__pycache__")
            dirnames.remove("__pycache__")


def _remove_empty_dirs(root: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        current = Path(dirpath)
        if current == root or _path_is_preserved(current, root):
            continue
        try:
            if not any(current.iterdir()):
                current.rmdir()
        except OSError:
            continue


def _path_is_preserved(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in _SKIP_DIRS for part in rel.parts)


def _read_bytes(path: Path) -> bytes:
    if not path.is_file():
        return b""
    return path.read_bytes()


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _rmtree(path: Path) -> None:
    if not path.exists():
        return

    def onerror(func, name, _exc_info):
        os.chmod(name, stat.S_IWRITE)
        func(name)

    shutil.rmtree(path, onerror=onerror)


def _sentence(text: str) -> str:
    cleaned = text.strip()
    if cleaned and not cleaned.endswith("."):
        return cleaned + "."
    return cleaned


def _tail(text: str, limit: int = 500) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "нет вывода"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def _now_label() -> str:
    return utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def _lock_active(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age > _LOCK_MAX_AGE_SECONDS:
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return False
    return _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, 0, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if _lock_active(self.path):
                return False
            try:
                self.path.unlink()
            except OSError:
                return False
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError:
                return False
        os.write(self._fd, str(os.getpid()).encode())
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass
