"""Управление systemd-службами bAWH через sudo-учётку из Параметров.

Та же учётка, что для перезапуска после обновления из git:
логин/пароль в app_settings (или UPDATE_SUDO_USER + sudoers).
С паролем — su -P; без пароля — sudo -n (deploy/bawh-update.sudoers).

Службы:
  bawh-web                 — Gunicorn
  bawh-scheduler           — опрос сети / железа / архив логов
  bawh-password-reports    — рассылка отчётов о паролях AD
  bawh-pc-reports          — рассылка отчётов о ПК
  bawh-vnc                 — WebSocket-прокси для noVNC
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from flask import current_app, has_app_context

logger = logging.getLogger(__name__)

SCHEDULER_UNIT = "bawh-scheduler"
WEB_UNIT = "bawh-web"
PASSWORD_REPORTS_UNIT = "bawh-password-reports"
PC_REPORTS_UNIT = "bawh-pc-reports"
VNC_UNIT = "bawh-vnc"

# Порядок для systemctl restart — должен совпадать с sudoers.
RESTART_UNITS = (
    SCHEDULER_UNIT,
    WEB_UNIT,
    PASSWORD_REPORTS_UNIT,
    PC_REPORTS_UNIT,
    VNC_UNIT,
)


class SystemdError(RuntimeError):
    """Не удалось выполнить systemctl."""


@dataclass(frozen=True)
class UnitStatus:
    """Состояние unit'а: active/enabled и сырые строки systemctl."""

    name: str
    active: bool
    enabled: bool
    active_state: str
    enabled_state: str
    available: bool
    detail: str = ""


@dataclass(frozen=True)
class EnsureResult:
    """Итог ensure_unit_running: что сделали и текущий статус."""

    status: UnitStatus
    started: bool
    enabled: bool
    message: str


def systemctl_path() -> str | None:
    for candidate in ("/usr/bin/systemctl", "/bin/systemctl"):
        if Path(candidate).is_file():
            return candidate
    return None


def resolve_sudo_credentials() -> tuple[str, str]:
    """Sudo-учётка из Параметров (логин + пароль).

    Без app context (фон без обёртки) не читаем current_app — иначе
    RuntimeError перечёркивает уже выполненное обновление.
    """
    if has_app_context():
        try:
            from app.services.settings_service import get_update_sudo_credentials

            creds = get_update_sudo_credentials()
            return creds.username, creds.password
        except Exception:  # noqa: BLE001 — UI/фон не должны падать из‑за БД
            return str(current_app.config.get("UPDATE_SUDO_USER") or "").strip(), ""
    return str(os.environ.get("UPDATE_SUDO_USER") or "").strip(), ""


def get_unit_status(unit: str = SCHEDULER_UNIT) -> UnitStatus:
    """Прочитать is-active / is-enabled без изменения состояния."""
    systemctl = systemctl_path()
    if systemctl is None:
        return UnitStatus(
            name=unit,
            active=False,
            enabled=False,
            active_state="unknown",
            enabled_state="unknown",
            available=False,
            detail="systemctl не найден (не Linux / не systemd)",
        )

    active_state = _systemctl_query(systemctl, "is-active", unit)
    enabled_state = _systemctl_query(systemctl, "is-enabled", unit)
    # В контейнере/без init systemctl есть, но unit'ы недоступны.
    if _looks_like_systemd_missing(active_state) or _looks_like_systemd_missing(
        enabled_state
    ):
        detail = active_state if _looks_like_systemd_missing(active_state) else enabled_state
        return UnitStatus(
            name=unit,
            active=False,
            enabled=False,
            active_state="unavailable",
            enabled_state="unavailable",
            available=False,
            detail=detail,
        )
    return UnitStatus(
        name=unit,
        active=active_state == "active",
        enabled=enabled_state in {"enabled", "enabled-runtime", "static"},
        active_state=active_state or "unknown",
        enabled_state=enabled_state or "unknown",
        available=True,
    )


def ensure_unit_running(unit: str) -> EnsureResult:
    """Включить и запустить unit, если ещё не active.

    Если unit-файла ещё нет в systemd (типично сразу после обновления
    из git), сначала вызывает sync_unit_files() и повторяет попытку.
    """
    status = get_unit_status(unit)
    if not status.available:
        return EnsureResult(
            status=status,
            started=False,
            enabled=False,
            message=status.detail or "systemd недоступен",
        )

    if status.active:
        return EnsureResult(
            status=status,
            started=False,
            enabled=False,
            message=f"Служба {unit} уже запущена ({status.active_state}).",
        )

    synced = False
    if _unit_file_missing(status, unit):
        sync_unit_files()
        synced = True
        status = get_unit_status(unit)

    systemctl = systemctl_path()
    assert systemctl is not None

    enabled_now = False
    try:
        if not status.enabled:
            _run_systemctl(systemctl, "enable", unit)
            enabled_now = True
        _run_systemctl(systemctl, "start", unit)
    except SystemdError as exc:
        if not synced and _looks_like_missing_unit(str(exc)):
            sync_unit_files()
            synced = True
            if not get_unit_status(unit).enabled:
                _run_systemctl(systemctl, "enable", unit)
                enabled_now = True
            _run_systemctl(systemctl, "start", unit)
        else:
            raise

    refreshed = get_unit_status(unit)
    if not refreshed.active:
        raise SystemdError(
            f"Не удалось запустить {unit}: "
            f"состояние {refreshed.active_state}. "
            "Проверьте sudo-учётку в Параметры → Управление службами."
        )
    parts = [f"Служба {unit} запущена."]
    if synced:
        parts.insert(0, "Unit-файлы установлены из deploy/.")
    if enabled_now:
        parts.append("Unit включён в автозагрузку.")
    return EnsureResult(
        status=refreshed,
        started=True,
        enabled=enabled_now,
        message=" ".join(parts),
    )


def ensure_scheduler_running() -> EnsureResult:
    """Совместимость: поднять bawh-scheduler (опрос сети)."""
    return ensure_unit_running(SCHEDULER_UNIT)


def stop_unit(unit: str) -> EnsureResult:
    """Остановить unit (тумблер отчёта выключен). Не disable — автозагрузка
    остаётся; при следующем enable/start из тумблера поднимется снова.
    """
    status = get_unit_status(unit)
    if not status.available:
        return EnsureResult(
            status=status,
            started=False,
            enabled=False,
            message=status.detail or "systemd недоступен",
        )
    if not status.active:
        return EnsureResult(
            status=status,
            started=False,
            enabled=False,
            message=f"Служба {unit} уже остановлена ({status.active_state}).",
        )
    systemctl = systemctl_path()
    assert systemctl is not None
    _run_systemctl(systemctl, "stop", unit)
    refreshed = get_unit_status(unit)
    return EnsureResult(
        status=refreshed,
        started=False,
        enabled=False,
        message=f"Служба {unit} остановлена.",
    )


def sync_unit_files(install_dir: str | Path | None = None) -> None:
    """Скопировать unit-файлы из deploy/ и daemon-reload.

    Нужно после обновления из git, чтобы появились новые службы отчётов.
    """
    root = Path(install_dir or _default_install_dir())
    script = root / "deploy" / "sync-systemd-units.sh"
    if not script.is_file():
        raise SystemdError(f"Нет скрипта синхронизации unit'ов: {script}")

    sudo_user, sudo_password = resolve_sudo_credentials()
    # Скрипт сам делает cp + daemon-reload; запускаем от root через sudo/su.
    result = _run_privileged_script(
        str(script),
        [str(root)],
        sudo_user=sudo_user,
        sudo_password=sudo_password,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemdError(
            f"Синхронизация systemd unit'ов завершилась с кодом "
            f"{result.returncode}. {detail}".strip()
            + " Проверьте sudo-учётку и deploy/bawh-update.sudoers."
        )


def run_service_restart(
    systemctl: str,
    sudo_user: str = "",
    sudo_password: str = "",
) -> subprocess.CompletedProcess:
    """Перезапуск web + опрос + службы отчётов (как после обновления)."""
    return _run_privileged(
        systemctl,
        ["restart", *RESTART_UNITS],
        sudo_user=sudo_user,
        sudo_password=sudo_password,
        timeout=120,
    )


def run_core_service_restart(
    systemctl: str,
    sudo_user: str = "",
    sudo_password: str = "",
) -> subprocess.CompletedProcess:
    """Fallback: только bawh-scheduler + bawh-web (старый sudoers)."""
    return _run_privileged(
        systemctl,
        ["restart", SCHEDULER_UNIT, WEB_UNIT],
        sudo_user=sudo_user,
        sudo_password=sudo_password,
        timeout=90,
    )


def restart_command(
    systemctl: str,
    sudo_user: str = "",
    *,
    with_password: bool = False,
) -> list[str]:
    """Команда перезапуска служб (совместимость с update_service)."""
    return _build_privileged_command(
        systemctl,
        ["restart", *RESTART_UNITS],
        sudo_user=sudo_user,
        with_password=with_password,
    )


def restart_units_hint() -> str:
    return "sudo systemctl restart " + " ".join(RESTART_UNITS)


def _default_install_dir() -> Path:
    if has_app_context():
        try:
            root = current_app.root_path
            # app/ → корень репозитория /opt/bawh
            return Path(root).resolve().parent
        except Exception:  # noqa: BLE001
            pass
    return Path(os.environ.get("BAWH_ROOT") or "/opt/bawh")


def _looks_like_systemd_missing(state: str) -> bool:
    text = (state or "").lower()
    return (
        "not been booted with systemd" in text
        or "failed to connect" in text
        or "system has not been booted" in text
        or "cannot talk to" in text
    )


def _unit_file_missing(status: UnitStatus, unit: str) -> bool:
    """Unit ещё не установлен в systemd (not-found / нет .service файла)."""
    if (status.enabled_state or "").lower() in {"not-found", "not found"}:
        return True
    if (status.active_state or "").lower() in {"not-found", "not found"}:
        return True
    service_name = f"{unit}.service"
    for directory in (
        Path("/etc/systemd/system"),
        Path("/lib/systemd/system"),
        Path("/usr/lib/systemd/system"),
    ):
        if (directory / service_name).is_file():
            return False
    # Нет файла и systemctl не сказал not-found (редко) — всё равно пробуем sync.
    # Не трогаем уже известные unit'ы вроде bawh-web / bawh-scheduler без нужды:
    # если enabled_state нормальный (disabled/enabled), файл должен быть.
    state = (status.enabled_state or "").lower()
    if state in {"disabled", "enabled", "enabled-runtime", "static", "masked", "indirect"}:
        return False
    return True


def _looks_like_missing_unit(detail: str) -> bool:
    text = (detail or "").lower()
    return (
        "does not exist" in text
        or "not found" in text
        or "не найден" in text
        or "unit file" in text and "exist" in text
    )


def _systemctl_query(systemctl: str, action: str, unit: str) -> str:
    """is-active / is-enabled: без привилегий обычно достаточно."""
    try:
        result = subprocess.run(
            [systemctl, action, unit],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("systemctl %s %s: %s", action, unit, exc)
        return "unknown"
    text = (result.stdout or result.stderr or "").strip()
    if not text:
        return "unknown"
    return text.splitlines()[0].strip()


def _run_systemctl(systemctl: str, action: str, *units: str) -> None:
    sudo_user, sudo_password = resolve_sudo_credentials()
    result = _run_privileged(
        systemctl,
        [action, *units],
        sudo_user=sudo_user,
        sudo_password=sudo_password,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemdError(
            f"systemctl {action} {' '.join(units)} завершился с кодом "
            f"{result.returncode}. {detail}".strip()
            + " Проверьте sudo-учётку в Параметры → Управление службами "
            "(или deploy/bawh-update.sudoers)."
        )


def _build_privileged_command(
    systemctl: str,
    args: list[str],
    *,
    sudo_user: str = "",
    with_password: bool = False,
) -> list[str]:
    if with_password:
        user = (sudo_user or "root").strip() or "root"
        remote_args = " ".join(_shell_quote(a) for a in args)
        if user == "root":
            remote = f"{systemctl} {remote_args}"
        else:
            remote = (
                f"printf '%s\\n' \"$BAWH_SU_PASS\" | "
                f"sudo -S -p '' {systemctl} {remote_args}"
            )
        return ["su", "-P", "-w", "BAWH_SU_PASS", user, "-c", remote]

    command = ["sudo", "-n"]
    user = (sudo_user or "").strip()
    if user:
        command.extend(["-u", user])
    command.append(systemctl)
    command.extend(args)
    return command


def _run_privileged(
    systemctl: str,
    args: list[str],
    *,
    sudo_user: str = "",
    sudo_password: str = "",
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    password = sudo_password or ""
    if password:
        command = _build_privileged_command(
            systemctl, args, sudo_user=sudo_user, with_password=True
        )
        return _run_su_with_password(command, password, timeout=timeout)
    command = _build_privileged_command(systemctl, args, sudo_user=sudo_user)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _run_privileged_script(
    script: str,
    args: list[str],
    *,
    sudo_user: str = "",
    sudo_password: str = "",
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Запуск deploy/sync-systemd-units.sh от root."""
    bash = "/usr/bin/bash"
    password = sudo_password or ""
    if password:
        user = (sudo_user or "root").strip() or "root"
        remote_args = " ".join(_shell_quote(a) for a in [script, *args])
        if user == "root":
            remote = f"{bash} {remote_args}"
        else:
            remote = (
                f"printf '%s\\n' \"$BAWH_SU_PASS\" | "
                f"sudo -S -p '' {bash} {remote_args}"
            )
        command = ["su", "-P", "-w", "BAWH_SU_PASS", user, "-c", remote]
        return _run_su_with_password(command, password, timeout=timeout)

    command = ["sudo", "-n"]
    user = (sudo_user or "").strip()
    if user:
        command.extend(["-u", user])
    command.extend([bash, script, *args])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _run_su_with_password(
    command: list[str],
    password: str,
    *,
    timeout: int = 90,
) -> subprocess.CompletedProcess:
    """Запускает su -P … и вводит пароль на запросе в pty."""
    import pty
    import select
    import time

    env = os.environ.copy()
    env["BAWH_SU_PASS"] = password

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            env=env,
        )
    finally:
        os.close(slave)

    output = bytearray()
    password_sent = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise subprocess.TimeoutExpired(command, timeout)
            ready, _, _ = select.select([master], [], [], min(remaining, 0.5))
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                text = output.decode("utf-8", "replace").lower()
                if not password_sent and (
                    "password" in text or "пароль" in text or "passwort" in text
                ):
                    os.write(master, (password + "\n").encode())
                    password_sent = True
            if proc.poll() is not None:
                while True:
                    ready, _, _ = select.select([master], [], [], 0.05)
                    if not ready:
                        break
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                break
        returncode = proc.wait(timeout=1)
    finally:
        try:
            os.close(master)
        except OSError:
            pass

    text = output.decode("utf-8", "replace")
    if returncode != 0 and not password_sent:
        text = (text + "\nне дождались запроса пароля su").strip()
    return subprocess.CompletedProcess(command, returncode, text, "")


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
