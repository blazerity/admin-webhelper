"""Обновление из git: копия обязательна, откат возвращает файлы.

Сеть и pip здесь не вызываются. Скачивание подменяется, кроме одного
теста локального репозитория. Миграции базы тоже подменяются: иначе
subprocess дошёл бы до настоящего .env выше по дереву каталогов.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.services import update_service
from app.services.update_service import (
    UpdateError,
    UpdateResult,
    begin_update,
    clone_repository,
    create_backup,
    list_backups,
    perform_rollback,
    perform_update,
    prune_backups,
    remote_head,
    restart_command,
    validate_branch,
    validate_remote_url,
)

SHA = "a" * 40
URL = "https://example.com/org/bAWH.git"


@pytest.fixture
def no_side_effects(monkeypatch):
    monkeypatch.setattr(update_service, "pip_install", lambda root: None)
    monkeypatch.setattr(update_service, "db_upgrade", lambda root: None)
    monkeypatch.setattr(update_service, "align_git_head", lambda root, url, branch: None)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "bawh"
    root.mkdir()
    (root / "wsgi.py").write_text("old", encoding="utf-8")
    (root / "obsolete.py").write_text("gone", encoding="utf-8")
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / "local.db").write_text("db", encoding="utf-8")
    venv = root / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home", encoding="utf-8")
    library = root / "script_library"
    library.mkdir()
    (library / "keep.txt").write_text("keep", encoding="utf-8")
    return root


def _install_fake_remote(monkeypatch, sha: str = SHA, body: str = "new", version: str = "0.2.0") -> None:
    def head(url, branch, *, cwd):
        assert url == URL
        assert branch == "main"
        return sha

    def clone(url, branch, dest: Path):
        dest.mkdir(parents=True)
        (dest / "wsgi.py").write_text(body, encoding="utf-8")
        (dest / "added.py").write_text("added", encoding="utf-8")
        (dest / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        return sha, "Новая версия"

    monkeypatch.setattr(update_service, "remote_head", head)
    monkeypatch.setattr(update_service, "clone_repository", clone)


def _update(root: Path) -> UpdateResult:
    return perform_update(root=root, remote_url=URL, branch="main", keep=5)


def test_remote_url_must_be_public_https():
    assert validate_remote_url(" https://github.com/org/bAWH.git ") == "https://github.com/org/bAWH.git"
    assert validate_branch("release/1.2") == "release/1.2"
    for bad in (
        "http://github.com/org/bAWH.git",
        "file:///tmp/repo",
        "https://user:token@github.com/org/bAWH.git",
        "https://github.com/org/bAWH.git && rm -rf /",
        "",
    ):
        with pytest.raises(UpdateError):
            validate_remote_url(bad)
    for bad_branch in ("../main", "-delete", "feature//x", "/main"):
        with pytest.raises(UpdateError):
            validate_branch(bad_branch)


def test_restart_command_includes_sudo_user():
    assert restart_command("/usr/bin/systemctl") == [
        "sudo",
        "-n",
        "/usr/bin/systemctl",
        "restart",
        "bawh-scheduler",
        "bawh-web",
    ]
    assert restart_command("/usr/bin/systemctl", "root") == [
        "sudo",
        "-n",
        "-u",
        "root",
        "/usr/bin/systemctl",
        "restart",
        "bawh-scheduler",
        "bawh-web",
    ]
    assert restart_command("/usr/bin/systemctl", "root", with_password=True) == [
        "su",
        "-P",
        "-w",
        "BAWH_SU_PASS",
        "root",
        "-c",
        "/usr/bin/systemctl restart bawh-scheduler bawh-web",
    ]
    assert restart_command("/usr/bin/systemctl", "deploy", with_password=True) == [
        "su",
        "-P",
        "-w",
        "BAWH_SU_PASS",
        "deploy",
        "-c",
        "printf '%s\\n' \"$BAWH_SU_PASS\" | sudo -S -p '' /usr/bin/systemctl restart bawh-scheduler bawh-web",
    ]


def test_backup_is_created_before_files_change(tmp_path, monkeypatch, no_side_effects):
    root = _project(tmp_path)
    seen = {}

    def clone(url, branch, dest: Path):
        backups = list_backups(root)
        assert len(backups) == 1
        tree = root / "backups" / backups[0].id / "tree"
        assert (tree / "wsgi.py").read_text(encoding="utf-8") == "old"
        assert not (tree / ".env").exists()
        assert not (tree / ".venv").exists()
        assert not (tree / "script_library").exists()
        assert not (tree / "local.db").exists()
        seen["during"] = (root / "wsgi.py").read_text(encoding="utf-8")
        dest.mkdir(parents=True)
        (dest / "wsgi.py").write_text("new", encoding="utf-8")
        (dest / "added.py").write_text("added", encoding="utf-8")
        (dest / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        return SHA, "Новая версия"

    monkeypatch.setattr(update_service, "remote_head", lambda url, branch, cwd: SHA)
    monkeypatch.setattr(update_service, "clone_repository", clone)

    result = _update(root)

    assert seen["during"] == "old"
    assert result.changed is True
    assert (root / "wsgi.py").read_text(encoding="utf-8") == "new"
    assert (root / "added.py").read_text(encoding="utf-8") == "added"
    assert not (root / "obsolete.py").exists()
    assert (root / ".env").read_text(encoding="utf-8") == "SECRET=1"
    assert (root / "local.db").read_text(encoding="utf-8") == "db"
    assert (root / ".venv" / "pyvenv.cfg").is_file()
    assert (root / "script_library" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (root / "backups" / "installed.json").is_file()
    installed = (root / "backups" / "installed.json").read_text(encoding="utf-8")
    assert "0.2.0" in installed
    assert "Установлена версия 0.2.0" in result.message
    backups = list_backups(root)
    assert backups[0].version == "0.1.0"


def test_failed_backup_does_not_download(tmp_path, monkeypatch, no_side_effects):
    root = _project(tmp_path)
    called = {"clone": False}

    def clone(url, branch, dest):
        called["clone"] = True
        raise AssertionError("скачивание не должно начаться")

    monkeypatch.setattr(update_service, "remote_head", lambda url, branch, cwd: SHA)
    monkeypatch.setattr(update_service, "clone_repository", clone)
    monkeypatch.setattr(update_service, "create_backup", lambda root, reason: (_ for _ in ()).throw(OSError("нет места")))

    with pytest.raises(UpdateError, match="файлы не менялись"):
        _update(root)

    assert called["clone"] is False
    assert (root / "wsgi.py").read_text(encoding="utf-8") == "old"


def test_download_failure_leaves_the_tree_and_drops_unused_backup(tmp_path, monkeypatch, no_side_effects):
    root = _project(tmp_path)
    monkeypatch.setattr(update_service, "remote_head", lambda url, branch, cwd: SHA)

    def clone(url, branch, dest):
        raise UpdateError("сеть недоступна")

    monkeypatch.setattr(update_service, "clone_repository", clone)
    with pytest.raises(UpdateError, match="не менялись"):
        _update(root)
    assert (root / "wsgi.py").read_text(encoding="utf-8") == "old"
    assert list_backups(root) == []


def test_replace_failure_restores_backup(tmp_path, monkeypatch, no_side_effects):
    root = _project(tmp_path)
    (root / "requirements.txt").write_text("old\n", encoding="utf-8")
    _install_fake_remote(monkeypatch)

    def clone(url, branch, dest: Path):
        dest.mkdir(parents=True)
        (dest / "wsgi.py").write_text("new", encoding="utf-8")
        (dest / "requirements.txt").write_text("new\n", encoding="utf-8")
        (dest / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        return SHA, "Новая версия"

    monkeypatch.setattr(update_service, "clone_repository", clone)

    def pip_install(target: Path):
        if (target / "requirements.txt").read_text(encoding="utf-8") == "new\n":
            raise UpdateError("pip сломался")

    monkeypatch.setattr(update_service, "pip_install", pip_install)

    with pytest.raises(UpdateError, match="восстановлена"):
        _update(root)

    assert (root / "wsgi.py").read_text(encoding="utf-8") == "old"
    assert (root / "requirements.txt").read_text(encoding="utf-8") == "old\n"
    assert (root / ".env").read_text(encoding="utf-8") == "SECRET=1"
    assert list_backups(root)


def test_same_commit_skips_backup(tmp_path, monkeypatch, no_side_effects):
    root = _project(tmp_path)
    update_service._write_installed(root, SHA, "уже стоит", "0.1.0", "main", "")
    monkeypatch.setattr(update_service, "remote_head", lambda url, branch, cwd: SHA)
    monkeypatch.setattr(
        update_service,
        "clone_repository",
        lambda url, branch, dest: (_ for _ in ()).throw(AssertionError("клон не нужен")),
    )
    result = _update(root)
    assert result.changed is False
    assert "0.1.0" in result.message
    assert list_backups(root) == []
    assert (root / "wsgi.py").read_text(encoding="utf-8") == "old"


def test_check_for_updates_uses_version_numbers(app, tmp_path, monkeypatch):
    root = _project(tmp_path)
    other = "b" * 40
    app.config["PROJECT_ROOT"] = str(root)
    app.config["GIT_REMOTE_URL"] = URL
    app.config["GIT_BRANCH"] = "main"
    update_service._write_installed(root, SHA, "локально", "0.1.0", "main", "")
    monkeypatch.setattr(update_service, "remote_head", lambda url, branch, cwd: other)
    monkeypatch.setattr(update_service, "remote_version", lambda url, branch, commit, cwd: "0.2.0")
    with app.app_context():
        message = update_service.check_for_updates()
    assert message == "Доступно обновление: 0.1.0 → 0.2.0."
    remote = update_service._read_json(root / "backups" / "remote-check.json")
    assert remote["version"] == "0.2.0"
    assert remote["commit"] == other


def test_rollback_restores_files_and_keeps_secret(tmp_path, monkeypatch, no_side_effects):
    root = _project(tmp_path)
    _install_fake_remote(monkeypatch)
    updated = _update(root)
    assert updated.backup_id is not None

    rolled = perform_rollback(root=root, backup_id=updated.backup_id, branch="main", keep=5)

    assert (root / "wsgi.py").read_text(encoding="utf-8") == "old"
    assert (root / "obsolete.py").read_text(encoding="utf-8") == "gone"
    assert not (root / "added.py").exists()
    assert (root / ".env").read_text(encoding="utf-8") == "SECRET=1"
    assert (root / "script_library" / "keep.txt").read_text(encoding="utf-8") == "keep"
    safety = root / "backups" / rolled.backup_id / "tree" / "wsgi.py"
    assert safety.read_text(encoding="utf-8") == "new"


def test_rollback_without_safety_backup_does_not_touch_files(tmp_path, monkeypatch, no_side_effects):
    root = _project(tmp_path)
    _install_fake_remote(monkeypatch)
    updated = _update(root)
    monkeypatch.setattr(
        update_service,
        "create_backup",
        lambda root, reason: (_ for _ in ()).throw(OSError("нет места")),
    )
    with pytest.raises(UpdateError, match="откат отменён"):
        perform_rollback(root=root, backup_id=updated.backup_id, branch="main", keep=5)
    assert (root / "wsgi.py").read_text(encoding="utf-8") == "new"


def test_backup_id_cannot_escape_the_directory(tmp_path):
    root = _project(tmp_path)
    for bad in ("../.env", "..", "20261002-000000-none/../../.env", "not-an-id"):
        with pytest.raises(UpdateError):
            perform_rollback(root=root, backup_id=bad, branch="main", keep=5)
    assert (root / ".env").read_text(encoding="utf-8") == "SECRET=1"


def test_prune_keeps_newest_and_protected(tmp_path):
    root = _project(tmp_path)
    base = root / "backups"
    for name in (
        "20261002-120000-aaaaaaaaaaaa",
        "20261002-120001-bbbbbbbbbbbb",
        "20261002-120002-cccccccccccc",
    ):
        tree = base / name / "tree"
        tree.mkdir(parents=True)
        (tree / "wsgi.py").write_text(name, encoding="utf-8")
        (base / name / "manifest.json").write_text(
            '{"created_at":"t","commit":"","subject":"","reason":"before-update"}',
            encoding="utf-8",
        )
    prune_backups(root, 1, protect={"20261002-120000-aaaaaaaaaaaa"})
    left = {item.id for item in list_backups(root)}
    assert left == {"20261002-120002-cccccccccccc", "20261002-120000-aaaaaaaaaaaa"}


def test_begin_update_reports_success_without_restart(app, tmp_path, monkeypatch):
    root = _project(tmp_path)
    app.config["PROJECT_ROOT"] = str(root)
    app.config["GIT_REMOTE_URL"] = URL
    app.config["GIT_BRANCH"] = "main"
    app.config["UPDATE_RESTART"] = False

    def perform(**kwargs):
        return UpdateResult(True, "установлено", "20261002-120000-aaaaaaaaaaaa", SHA)

    monkeypatch.setattr(update_service, "perform_update", perform)
    with app.app_context():
        begin_update()
        for _ in range(50):
            operation = update_service._read_json(root / "backups" / "operation.json")
            if operation.get("state") == "success":
                break
            time.sleep(0.05)
    assert operation["state"] == "success"
    assert "установлено" in operation["message"]
    assert not (root / "backups" / ".lock").exists()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git не установлен")
def test_clone_repository_reads_local_branch(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    _git(source, "config", "core.autocrlf", "false")
    (source / "wsgi.py").write_text("v2\n", encoding="utf-8")
    _git(source, "add", "wsgi.py")
    _git(source, "commit", "-m", "v2")

    dest = tmp_path / "clone"
    commit, subject = clone_repository(str(source), "main", dest)
    assert (dest / "wsgi.py").read_text(encoding="utf-8") == "v2\n"
    assert commit == remote_head(str(source), "main", cwd=tmp_path)
    assert subject == "v2"


@pytest.mark.skipif(shutil.which("git") is None, reason="git не установлен")
def test_align_leaves_a_clean_clone_the_installer_can_pull(tmp_path):
    """Повторный install-debian12.sh делает git pull --ff-only. После кнопки «Обновить» это должно проходить."""
    from app.services.update_service import align_git_head

    source = tmp_path / "src"
    origin = tmp_path / "origin.git"
    install = tmp_path / "opt"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    _git(source, "config", "core.autocrlf", "false")
    (source / "wsgi.py").write_text("v1\n", encoding="utf-8")
    (source / ".gitignore").write_text(".env\n", encoding="utf-8")
    _git(source, "add", "wsgi.py", ".gitignore")
    _git(source, "commit", "-m", "v1")
    _git(source, "clone", "--bare", str(source), str(origin))
    _git(tmp_path, "clone", "--branch", "main", str(origin), str(install))
    _git(install, "config", "core.autocrlf", "false")
    (install / ".env").write_text("SECRET=1\n", encoding="utf-8")

    (source / "wsgi.py").write_text("v2\n", encoding="utf-8")
    _git(source, "add", "wsgi.py")
    _git(source, "commit", "-m", "v2")
    _git(source, "push", str(origin), "main")

    align_git_head(install, str(origin), "main")

    assert not (install / ".git" / "shallow").exists()
    assert (install / ".env").read_text(encoding="utf-8") == "SECRET=1\n"
    assert (install / "wsgi.py").read_text(encoding="utf-8") == "v2\n"
    status = subprocess.run(
        ["git", "-c", "safe.directory=*", "status", "--porcelain"],
        cwd=install,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""

    (source / "wsgi.py").write_text("v3\n", encoding="utf-8")
    _git(source, "add", "wsgi.py")
    _git(source, "commit", "-m", "v3")
    _git(source, "push", str(origin), "main")
    pull = subprocess.run(
        ["git", "-c", "safe.directory=*", "pull", "--ff-only", "origin", "main"],
        cwd=install,
        capture_output=True,
        text=True,
    )
    assert pull.returncode == 0, pull.stderr
    assert (install / "wsgi.py").read_text(encoding="utf-8") == "v3\n"
    assert (install / ".env").read_text(encoding="utf-8") == "SECRET=1\n"
