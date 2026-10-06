"""Настройки администратора: учётка PsExec, WMI discovery, опрос, обновление.

Эндпоинты:
- settings      GET/POST /admin/settings
- poll-run      POST /admin/poll-run — принудительный опрос сети
- hardware-poll-run POST /admin/hardware-poll-run — опрос железа Windows
- refresh-hostnames POST /admin/refresh-hostnames — WMI-имена на известных машинах
- updates       GET/POST /admin/updates

Формы на странице настроек различаются скрытым полем form:
psexec, discovery, poll, hardware_poll, smtp, test_smtp, update, vnc, tls.
Открытый пароль в шаблон и во flash не попадает.

PsExec: учётка только текущего администратора. Пустые пользователь
и пароль означают запуск от его входа на сайт. Пустой пароль при уже
заданном имени оставляет прежний шифротекст.

Discovery: глобальная учётка WMI для опроса (серийник / MAC / текущая УЗ).
Пароль — Fernet в app_settings. Пустые поля отключают WMI (или оставляют .env).

Опрос: интервал в app_settings; журнал прогонов — network_poll_runs;
кнопка «Запустить сейчас» вызывает ту же run_network_poll, что и планировщик.

Опрос железа: cron и журнал hardware_poll_runs на той же странице;
кнопка вызывает run_hardware_poll.

Обновление — отдельные кнопки на /admin/updates. Замена кода
начинается только после резервной копии, см. update_service.
Ветку выбирают там же: список приходит из git ls-remote, значение
хранится в app_settings и важнее GIT_BRANCH.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.authz import admin_required
from app.services import audit_service
from app.services.export_service import csv_attachment, export_hardware_poll_runs_csv, export_poll_runs_csv
from app.services.credential_service import (
    CredentialsNotConfigured,
    get_stored_credential,
    save_remote_admin_credentials,
)
from app.services.crypto_service import CryptoError, CryptoNotConfigured
from app.services.network_summary_service import get_scheduler_health
from app.services.hardware_poll_service import (
    HardwarePollError,
    HardwarePollInProgressError,
    load_recent_hardware_poll_runs,
    run_hardware_poll,
)
from app.services.hardware_poll_settings import get_hardware_poll_settings, set_hardware_poll_settings
from app.services.hostname_sweep_service import (
    HostnameSweepError,
    run_hostname_sweep,
)
from app.services.ping_service import (
    PollInProgressError,
    load_recent_poll_runs,
    run_network_poll,
)
from app.services.settings_service import (
    DiscoveryCredentialsError,
    UpdateSudoUserError,
    get_discovery_credential_view,
    get_poll_interval_seconds,
    get_update_sudo_view,
    save_discovery_credentials,
    save_update_sudo_credentials,
    set_poll_interval_seconds,
)
from app.services.password_expiry_settings import get_smtp_settings, set_smtp_settings
from app.services.password_mailer import PasswordMailerError, test_smtp_connection
from app.services.systemd_service import (
    VNC_UNIT,
    SystemdError,
    ensure_unit_running,
    get_unit_status,
    stop_unit,
)
from app.services.tls_pem import TlsPemError
from app.services.tls_service import TlsApplyError, apply_nginx_tls, save_certificate_files
from app.services.tls_settings import get_tls_settings
from app.services.update_service import (
    UpdateError,
    begin_rollback,
    begin_update,
    build_page,
    check_for_updates,
    refresh_remote_branches,
    save_update_branch,
)
from app.services.vnc_settings import clear_vnc_password, get_vnc_settings, set_vnc_settings

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _credential_view(user_id: int) -> tuple[str, str, bool]:
    """Имя и домен учётки PsExec этого пользователя.

    password_set — в его строке уже есть шифротекст пароля PsExec.
    Расшифровку на GET не вызываем: странице пароль не нужен.
    Пустые поля — запуск от входа на сайт, чужая строка сюда не попадает.
    """
    row = get_stored_credential(user_id)
    if row is None:
        return "", "", False
    return row.username or "", row.domain or "", bool(row.password_encrypted)


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        kind = (request.form.get("form") or "").strip()
        if kind == "psexec":
            try:
                saved = save_remote_admin_credentials(
                    request.form.get("username", ""),
                    request.form.get("domain", ""),
                    request.form.get("password", ""),
                    current_user.id,
                )
            except (CredentialsNotConfigured, CryptoNotConfigured, CryptoError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings"))
            if saved.username:
                flash("Учётка PsExec сохранена.", "success")
            else:
                flash(
                    "Поля PsExec пустые. Команда запустится от имени вашего входа на сайт.",
                    "success",
                )
            audit_service.log(current_user, "update", "admin_settings", "psexec")
            return redirect(url_for("admin.settings"))
        if kind == "discovery":
            try:
                saved = save_discovery_credentials(
                    request.form.get("discovery_username", ""),
                    request.form.get("discovery_domain", ""),
                    request.form.get("discovery_password", ""),
                )
            except (DiscoveryCredentialsError, CryptoNotConfigured, CryptoError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings"))
            if saved.username:
                flash("Учётка WMI для опроса сохранена.", "success")
            else:
                flash(
                    "Учётка WMI очищена. Серийник и MAC по WMI не запрашиваются "
                    "(если не заданы DISCOVERY_* в .env).",
                    "success",
                )
            audit_service.log(current_user, "update", "admin_settings", "discovery")
            return redirect(url_for("admin.settings"))
        if kind == "poll":
            raw = (request.form.get("poll_interval") or "").strip()
            try:
                value = int(raw)
            except ValueError:
                flash("Интервал должен быть целым числом секунд.", "danger")
                return redirect(url_for("admin.settings"))
            try:
                set_poll_interval_seconds(value)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings"))
            audit_service.log(
                current_user,
                "update",
                "admin_settings",
                "poll",
                detail=f"interval={value}",
            )
            flash("Интервал опроса сохранён.", "success")
            return redirect(url_for("admin.settings"))
        if kind == "hardware_poll":
            want_enabled = request.form.get("hardware_poll_enabled") == "1"
            try:
                set_hardware_poll_settings(
                    schedule_enabled=want_enabled,
                    schedule_cron=request.form.get("hardware_poll_cron", ""),
                )
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings") + "#hardware-poll")
            audit_service.log(
                current_user,
                "update",
                "admin_settings",
                "hardware_poll",
                detail=f"enabled={int(want_enabled)} cron={request.form.get('hardware_poll_cron', '')}",
            )
            flash("Расписание опроса железа сохранено.", "success")
            return redirect(url_for("admin.settings") + "#hardware-poll")
        if kind == "smtp":
            try:
                set_smtp_settings(
                    host=request.form.get("smtp_host", ""),
                    port=request.form.get("smtp_port", ""),
                    use_starttls=request.form.get("smtp_use_starttls") == "1",
                    from_address=request.form.get("smtp_from", ""),
                    username=request.form.get("smtp_user", ""),
                    password=request.form.get("smtp_password"),
                )
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings") + "#smtp")
            audit_service.log(current_user, "update", "admin_settings", "smtp")
            flash("Настройки SMTP сохранены.", "success")
            return redirect(url_for("admin.settings") + "#smtp")
        if kind == "test_smtp":
            try:
                message = test_smtp_connection()
            except PasswordMailerError as exc:
                flash(str(exc), "danger")
            else:
                flash(message, "success")
            return redirect(url_for("admin.settings") + "#smtp")
        if kind == "update":
            try:
                saved = save_update_sudo_credentials(
                    request.form.get("update_sudo_user", ""),
                    request.form.get("update_sudo_password", ""),
                )
            except (UpdateSudoUserError, CryptoNotConfigured, CryptoError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings"))
            if saved.username:
                if saved.password_set:
                    flash(
                        f"Учётка управления службами сохранена "
                        f"({saved.username}, пароль задан).",
                        "success",
                    )
                else:
                    flash(
                        f"Sudo-пользователь: {saved.username}. "
                        "Пароль не задан — нужен sudoers или укажите пароль.",
                        "success",
                    )
            else:
                flash("Учётка управления службами очищена.", "success")
            audit_service.log(current_user, "update", "admin_settings", "update_sudo")
            return redirect(url_for("admin.settings") + "#service-control")
        if kind == "vnc":
            want_enabled = request.form.get("vnc_gateway_enabled") == "1"
            try:
                set_vnc_settings(
                    gateway_enabled=want_enabled,
                    port=request.form.get("vnc_port", ""),
                    password=request.form.get("vnc_password"),
                )
                if request.form.get("vnc_password_clear") == "1":
                    clear_vnc_password()
            except (ValueError, CryptoNotConfigured, CryptoError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings") + "#vnc")
            try:
                if want_enabled:
                    ensure = ensure_unit_running(VNC_UNIT)
                    flash(
                        f"VNC-шлюз включён (порт агента {request.form.get('vnc_port') or '5900'}). "
                        f"{ensure.message}",
                        "success",
                    )
                else:
                    stopped = stop_unit(VNC_UNIT)
                    flash(
                        f"VNC-шлюз выключен. Подключение из браузера не будет работать. "
                        f"{stopped.message}",
                        "success",
                    )
            except SystemdError as exc:
                flash(
                    f"Настройки VNC сохранены, но службу bawh-vnc переключить не удалось: {exc}",
                    "warning",
                )
            audit_service.log(
                current_user,
                "update",
                "admin_settings",
                "vnc",
                detail=f"enabled={int(want_enabled)} port={request.form.get('vnc_port', '')}",
            )
            return redirect(url_for("admin.settings") + "#vnc")
        if kind == "tls":
            action = (request.form.get("tls_action") or "save").strip()
            server_name = request.form.get("tls_server_name", "")
            redirect_http = request.form.get("tls_redirect_http") == "1"
            secure_cookie = request.form.get("tls_secure_cookie") == "1"
            cert_pem = request.form.get("tls_cert_pem") or ""
            key_pem = request.form.get("tls_key_pem") or ""
            cert_file = request.files.get("tls_cert_file")
            key_file = request.files.get("tls_key_file")
            if cert_file and cert_file.filename:
                cert_pem = cert_file.read().decode("utf-8", "replace")
            if key_file and key_file.filename:
                key_pem = key_file.read().decode("utf-8", "replace")
            try:
                if action == "disable":
                    message = apply_nginx_tls(
                        enable=False,
                        redirect=False,
                        server_name=server_name,
                        secure_cookie=False,
                    )
                    flash(message, "success")
                elif action == "apply":
                    message = apply_nginx_tls(
                        enable=True,
                        redirect=redirect_http,
                        server_name=server_name,
                        secure_cookie=secure_cookie,
                        cert_pem=cert_pem,
                        key_pem=key_pem,
                    )
                    flash(message, "success")
                    if secure_cookie:
                        flash(
                            "SESSION_COOKIE_SECURE записан в .env. "
                            "Чтобы флаг применился сразу, перезапустите службы "
                            "(Параметры → Управление службами / обновление).",
                            "warning",
                        )
                else:
                    if not (cert_pem.strip() and key_pem.strip()):
                        flash("Вставьте или загрузите сертификат и ключ.", "danger")
                        return redirect(url_for("admin.settings") + "#tls")
                    info = save_certificate_files(cert_pem, key_pem, server_name)
                    flash(
                        f"Сертификат сохранён ({info.subject}). "
                        "Нажмите «Включить HTTPS», чтобы Nginx начал слушать 443.",
                        "success",
                    )
            except (TlsPemError, ValueError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings") + "#tls")
            except TlsApplyError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings") + "#tls")
            except SystemdError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings") + "#tls")
            audit_service.log(
                current_user,
                "update",
                "admin_settings",
                "tls",
                detail=action,
            )
            return redirect(url_for("admin.settings") + "#tls")
        flash("Неизвестная форма.", "warning")
        return redirect(url_for("admin.settings"))

    username, domain, password_set = _credential_view(current_user.id)
    discovery = get_discovery_credential_view()
    update_sudo = get_update_sudo_view()
    return render_template(
        "admin/settings.html",
        username=username,
        domain=domain,
        password_set=password_set,
        discovery_username=discovery.username,
        discovery_domain=discovery.domain,
        discovery_password_set=discovery.password_set,
        poll_interval=get_poll_interval_seconds(),
        poll_runs=load_recent_poll_runs(),
        hardware_poll_settings=get_hardware_poll_settings(),
        hardware_poll_runs=load_recent_hardware_poll_runs(),
        scheduler_health=get_scheduler_health(),
        smtp=get_smtp_settings(),
        update_sudo_user=update_sudo.username,
        update_sudo_password_set=update_sudo.password_set,
        vnc=get_vnc_settings(),
        vnc_unit=get_unit_status(VNC_UNIT),
        tls=get_tls_settings(),
    )


@bp.get("/poll-runs/export.csv")
@admin_required
def export_poll_runs():
    """CSV журнала прогонов опроса (admin)."""
    return csv_attachment(export_poll_runs_csv(limit=200), "poll_runs.csv")


@bp.get("/hardware-poll-runs/export.csv")
@admin_required
def export_hardware_poll_runs():
    """CSV журнала прогонов опроса железа (admin)."""
    return csv_attachment(
        export_hardware_poll_runs_csv(limit=200), "hardware_poll_runs.csv"
    )


@bp.route("/hardware-poll-run", methods=["POST"])
@admin_required
def hardware_poll_run():
    """Принудительный опрос железа Windows (тот же код, что у планировщика)."""
    try:
        result = run_hardware_poll(mode="manual")
    except HardwarePollInProgressError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("admin.settings") + "#hardware-poll")
    except HardwarePollError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("admin.settings") + "#hardware-poll")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Сбой ручного опроса железа")
        flash(f"Ошибка опроса железа: {exc}", "danger")
        return redirect(url_for("admin.settings") + "#hardware-poll")

    flash(
        "Опрос железа: проверено {scanned}, онлайн {online}, "
        "собрано {collected}, изменено {changed}, офлайн {offline}, "
        "без WMI {no_wmi}, чужой IP {mismatched}, ошибок {errors}.".format(
            **result.as_dict()
        ),
        "success",
    )
    return redirect(url_for("admin.settings") + "#hardware-poll")


@bp.route("/poll-run", methods=["POST"])
@admin_required
def poll_run():
    """Принудительный полный опрос сети (тот же код, что у планировщика)."""
    try:
        stats = run_network_poll(mode="manual")
    except PollInProgressError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("admin.settings"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Сбой ручного опроса сети")
        flash(f"Ошибка опроса: {exc}", "danger")
        return redirect(url_for("admin.settings"))

    flash(
        "Опрос завершён: проверено {scanned}, онлайн {online}, "
        "офлайн {offline}, ошибок {errors}.".format(**stats),
        "success",
    )
    return redirect(url_for("admin.settings"))


@bp.route("/refresh-hostnames", methods=["POST"])
@admin_required
def refresh_hostnames():
    """Перечитать hostname с машин по WMI. Строки не сливает."""
    try:
        result = run_hostname_sweep()
    except HostnameSweepError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("admin.settings"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Сбой обновления имён устройств")
        flash(f"Ошибка обновления имён: {exc}", "danger")
        return redirect(url_for("admin.settings"))

    audit_service.log(
        current_user,
        "update",
        "devices",
        "hostname-sweep",
        detail=(
            f"scanned={result.scanned} updated={result.updated} "
            f"offline={result.offline} no_wmi={result.no_wmi} "
            f"errors={result.errors}"
        ),
    )
    flash(
        "Имена с машин: проверено {scanned}, изменено {updated}, "
        "без изменений {unchanged}, офлайн {offline}, "
        "без WMI {no_wmi}, ошибок {errors}.".format(**result.as_dict()),
        "success",
    )
    return redirect(url_for("admin.settings"))


@bp.route("/updates", methods=["GET", "POST"])
@admin_required
def updates():
    if request.method == "POST":
        kind = (request.form.get("form") or "").strip()
        try:
            if kind == "check":
                flash(check_for_updates(), "info")
            elif kind == "branches":
                names = refresh_remote_branches()
                flash(f"Список веток обновлён: {len(names)}.", "info")
            elif kind == "branch":
                chosen = (request.form.get("branch") or "").strip()
                flash(save_update_branch(chosen), "success")
                audit_service.log(
                    current_user,
                    "update",
                    "app",
                    "git-branch",
                    detail=chosen,
                )
            elif kind == "update":
                begin_update()
                audit_service.log(current_user, "update", "app", "git", detail="begin_update")
                flash("Обновление запущено. Сначала создаётся резервная копия.", "info")
            elif kind == "rollback":
                backup_id = request.form.get("backup_id", "")
                begin_rollback(backup_id)
                audit_service.log(
                    current_user,
                    "rollback",
                    "app",
                    backup_id or "backup",
                    detail="begin_rollback",
                )
                flash("Откат запущен. Текущая версия тоже сохраняется в копию.", "info")
            else:
                flash("Неизвестная форма.", "warning")
        except UpdateError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("admin.updates"))

    return render_template("admin/updates.html", status=build_page())


@bp.get("/audit")
@admin_required
def audit_log():
    """Простой список admin_audit_log (разметку может уточнить A2)."""
    entries = audit_service.list_audit_entries(limit=200)
    return render_template("admin/audit.html", entries=entries)
