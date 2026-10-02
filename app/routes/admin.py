"""Настройки администратора: учётка PsExec, WMI discovery, опрос, обновление.

Эндпоинты:
- settings      GET/POST /admin/settings
- poll-run      POST /admin/poll-run — принудительный опрос сети
- updates       GET/POST /admin/updates

Формы на странице настроек различаются скрытым полем form:
psexec, discovery, poll или update. Открытый пароль в шаблон и во flash не попадает.

PsExec: учётка только текущего администратора. Пустые пользователь
и пароль означают запуск от его входа на сайт. Пустой пароль при уже
заданном имени оставляет прежний шифротекст.

Discovery: глобальная учётка WMI для опроса (серийник / MAC / текущая УЗ).
Пароль — Fernet в app_settings. Пустые поля отключают WMI (или оставляют .env).

Опрос: интервал в app_settings; журнал прогонов — network_poll_runs;
кнопка «Запустить сейчас» вызывает ту же run_network_poll, что и планировщик.

Обновление — отдельные кнопки на /admin/updates. Замена кода
начинается только после резервной копии, см. update_service.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.authz import admin_required
from app.services.credential_service import (
    CredentialsNotConfigured,
    get_stored_credential,
    save_remote_admin_credentials,
)
from app.services.crypto_service import CryptoError, CryptoNotConfigured
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
from app.services.update_service import UpdateError, begin_rollback, begin_update, build_page, check_for_updates

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
            flash("Интервал опроса сохранён.", "success")
            return redirect(url_for("admin.settings"))
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
                        f"Sudo-учётка сохранена ({saved.username}, пароль задан).",
                        "success",
                    )
                else:
                    flash(
                        f"Sudo-пользователь: {saved.username}. "
                        "Пароль не задан — нужен sudoers или укажите пароль.",
                        "success",
                    )
            else:
                flash("Sudo-учётка очищена.", "success")
            return redirect(url_for("admin.settings"))
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
        update_sudo_user=update_sudo.username,
        update_sudo_password_set=update_sudo.password_set,
    )


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


@bp.route("/updates", methods=["GET", "POST"])
@admin_required
def updates():
    if request.method == "POST":
        kind = (request.form.get("form") or "").strip()
        try:
            if kind == "check":
                flash(check_for_updates(), "info")
            elif kind == "update":
                begin_update()
                flash("Обновление запущено. Сначала создаётся резервная копия.", "info")
            elif kind == "rollback":
                begin_rollback(request.form.get("backup_id", ""))
                flash("Откат запущен. Текущая версия тоже сохраняется в копию.", "info")
            else:
                flash("Неизвестная форма.", "warning")
        except UpdateError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("admin.updates"))

    return render_template("admin/updates.html", status=build_page())
