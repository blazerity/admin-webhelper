"""Настройки администратора: учётка PsExec, интервал опроса, обновление из git.

Эндпоинты:
- settings   GET/POST /admin/settings
- updates    GET/POST /admin/updates

Две формы на странице настроек различаются скрытым полем form:
psexec или poll. Открытый пароль в шаблон и во flash не попадает.
Пустой пароль при сохранении оставляет прежний шифротекст —
так устроен save_remote_admin_credentials.

Обновление — отдельные кнопки на /admin/updates. Замена кода
начинается только после резервной копии, см. update_service.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.authz import admin_required
from app.services.credential_service import (
    CredentialsNotConfigured,
    get_stored_credential,
    save_remote_admin_credentials,
)
from app.services.crypto_service import CryptoError, CryptoNotConfigured
from app.services.settings_service import get_poll_interval_seconds, set_poll_interval_seconds
from app.services.update_service import UpdateError, begin_rollback, begin_update, build_page, check_for_updates

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _credential_view() -> tuple[str, str, bool]:
    """Имя и домен для формы. password_set — в базе уже есть шифротекст.

    Расшифровку на GET не вызываем: странице пароль не нужен.
    «Оставьте пустым, чтобы не менять» верно только для уже сохранённого
    шифротекста. Пароль из PSEXEC_PASSWORD в шаблон не подставляется.
    """
    row = get_stored_credential()
    if row is not None and (row.username or row.password_encrypted):
        return row.username or "", row.domain or "", bool(row.password_encrypted)
    username = (current_app.config.get("PSEXEC_USERNAME") or "").strip()
    domain = (current_app.config.get("PSEXEC_DOMAIN") or "").strip()
    return username, domain, False


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        kind = (request.form.get("form") or "").strip()
        if kind == "psexec":
            try:
                save_remote_admin_credentials(
                    request.form.get("username", ""),
                    request.form.get("domain", ""),
                    request.form.get("password", ""),
                    current_user.id,
                )
            except (CredentialsNotConfigured, CryptoNotConfigured, CryptoError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.settings"))
            flash("Учётка PsExec сохранена.", "success")
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
        flash("Неизвестная форма.", "warning")
        return redirect(url_for("admin.settings"))

    username, domain, password_set = _credential_view()
    return render_template(
        "admin/settings.html",
        username=username,
        domain=domain,
        password_set=password_set,
        poll_interval=get_poll_interval_seconds(),
    )


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
