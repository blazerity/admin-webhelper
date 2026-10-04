"""UI модуля уведомлений о сроке паролей AD.

Доступ — только администраторам bAWH (LDAP-группа), без отдельного Basic Auth.
Отчёт — отдельный пункт верхнего меню; настройки — в разделе «Настройки».
"""

from __future__ import annotations

import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.authz import admin_required, user_can_view_password_expiry
from app.services.password_ad_client import PasswordAdError, verify_password_directory_access
from app.services.password_expiry_service import (
    RunInProgressError,
    load_last_report,
    load_last_run,
    notify_users_now,
    run_password_expiry,
)
from app.services.password_expiry_settings import (
    get_ldap_bind_settings,
    get_password_expiry_settings,
    get_smtp_settings,
    set_ldap_bind_settings,
    set_password_expiry_settings,
    set_smtp_settings,
)
from app.services.password_mailer import PasswordMailerError, test_smtp_connection
from app.services.report_toggle_service import apply_report_toggle, scheduler_status
from app.services.systemd_service import SystemdError, ensure_scheduler_running

logger = logging.getLogger(__name__)

bp = Blueprint("password_expiry", __name__, url_prefix="/password-expiry")


@bp.route("/", methods=["GET"])
@login_required
def dashboard():
    if not user_can_view_password_expiry(current_user):
        abort(403)
    report = load_last_report()
    run = load_last_run()
    settings = get_password_expiry_settings()
    q = (request.args.get("q") or "").strip().lower()
    section = (request.args.get("section") or "attention").strip()

    rows = []
    if report:
        if section == "upcoming":
            rows = report.upcoming
        elif section == "overdue":
            rows = report.overdue
        elif section == "resolved":
            rows = report.resolved
        elif section == "all":
            rows = report.all_users
        else:
            rows = list(report.overdue) + list(report.upcoming)
        if q:
            rows = [
                row
                for row in rows
                if q in row.username.lower()
                or q in row.full_name.lower()
                or q in row.email.lower()
            ]

    ldap = get_ldap_bind_settings()
    return render_template(
        "password_expiry/dashboard.html",
        report=report,
        run=run,
        settings=settings,
        rows=rows,
        q=q,
        section=section,
        smtp=get_smtp_settings(),
        ldap=ldap,
        scheduler=scheduler_status(),
        can_toggle=bool(getattr(current_user, "is_admin", False)),
    )


@bp.route("/toggle", methods=["POST"])
@admin_required
def toggle_schedule():
    """Сверх-тумблер: включить рассылку отчётов и службу bawh-scheduler."""
    enabled = request.form.get("schedule_enabled") == "1"

    def _set(value: bool) -> None:
        set_password_expiry_settings(schedule_enabled=value)

    result = apply_report_toggle(
        enabled=enabled,
        set_enabled=_set,
        label="Пароли AD",
    )
    category = "warning" if "не удалось" in result.message else "success"
    flash(result.message, category)
    return redirect(url_for("password_expiry.dashboard"))


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    if request.method == "POST":
        kind = (request.form.get("form") or "").strip()
        if kind == "module":
            want_enabled = request.form.get("schedule_enabled") == "1"
            try:
                set_password_expiry_settings(
                    max_pwd_age_days=int(request.form.get("max_pwd_age_days") or 0),
                    first_warning_days=int(request.form.get("first_warning_days") or 0),
                    daily_warning_threshold=int(
                        request.form.get("daily_warning_threshold") or 0
                    ),
                    instructions_url=request.form.get("instructions_url", ""),
                    excluded_ou=request.form.get("excluded_ou", ""),
                    search_base=request.form.get("search_base", ""),
                    schedule_enabled=want_enabled,
                    schedule_cron=request.form.get("schedule_cron", ""),
                    admin_recipients=request.form.get("admin_recipients", ""),
                )
            except (ValueError, TypeError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("password_expiry.settings_page"))
            flash("Настройки модуля сохранены.", "success")
            if want_enabled:
                try:
                    ensure = ensure_scheduler_running()
                    if ensure.started or ensure.enabled:
                        flash(ensure.message, "info")
                except SystemdError as exc:
                    flash(str(exc), "warning")
            return redirect(url_for("password_expiry.settings_page"))

        if kind == "ldap_bind":
            try:
                set_ldap_bind_settings(
                    bind_dn=request.form.get("ldap_bind_dn", ""),
                    bind_password=request.form.get("ldap_bind_password"),
                )
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("password_expiry.settings_page"))
            flash("Учётка LDAP для модуля сохранена.", "success")
            return redirect(url_for("password_expiry.settings_page"))

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
                return redirect(url_for("password_expiry.settings_page"))
            flash("Настройки SMTP сохранены.", "success")
            return redirect(url_for("password_expiry.settings_page"))

        if kind == "test_ldap":
            try:
                message = verify_password_directory_access()
            except PasswordAdError as exc:
                flash(str(exc), "danger")
            else:
                flash(message, "success")
            return redirect(url_for("password_expiry.settings_page"))

        if kind == "test_smtp":
            try:
                message = test_smtp_connection()
            except PasswordMailerError as exc:
                flash(str(exc), "danger")
            else:
                flash(message, "success")
            return redirect(url_for("password_expiry.settings_page"))

        flash("Неизвестная форма.", "danger")
        return redirect(url_for("password_expiry.settings_page"))

    ldap = get_ldap_bind_settings()
    return render_template(
        "password_expiry/settings.html",
        settings=get_password_expiry_settings(),
        smtp=get_smtp_settings(),
        ldap=ldap,
    )


@bp.route("/run", methods=["POST"])
@admin_required
def run_now():
    dry_run = request.form.get("dry_run") == "1"
    try:
        result = run_password_expiry(
            send_emails=not dry_run,
            mode="dry-run" if dry_run else "web",
        )
    except RunInProgressError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("password_expiry.dashboard"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Пароли AD: сбой ручного прогона")
        flash(f"Ошибка прогона: {exc}", "danger")
        return redirect(url_for("password_expiry.dashboard"))

    if result.exit_code == 0:
        label = "Тестовый прогон" if dry_run else "Прогон"
        flash(
            f"{label} завершён: пользователей {result.users_count}, "
            f"писем {result.sent_count}, upcoming {result.upcoming_count}, "
            f"overdue {result.overdue_count}.",
            "success",
        )
    else:
        flash(result.error or f"Прогон завершился с кодом {result.exit_code}", "danger")
    return redirect(url_for("password_expiry.dashboard"))


@bp.route("/pause", methods=["POST"])
@admin_required
def pause_mail():
    clear = request.form.get("clear") == "1"
    until = (request.form.get("until") or "").strip()
    try:
        if clear:
            set_password_expiry_settings(clear_pause=True)
            flash("Пауза рассылки снята.", "success")
        else:
            if not until:
                flash("Укажите дату окончания паузы.", "danger")
            else:
                set_password_expiry_settings(pause_user_mail_until=until)
                flash(f"Пауза пользовательских писем до {until} включительно.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("password_expiry.dashboard"))


@bp.route("/notify", methods=["POST"])
@admin_required
def notify_selected():
    usernames = request.form.getlist("username")
    try:
        result = notify_users_now(usernames, send_emails=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Пароли AD: сбой ручной рассылки")
        flash(f"Ошибка рассылки: {exc}", "danger")
        return redirect(url_for("password_expiry.dashboard"))

    if result.exit_code == 0:
        flash(f"Напоминание отправлено: {result.sent_count} из {result.users_count}.", "success")
    else:
        flash(result.error or "Рассылка не выполнена.", "danger")
    return redirect(url_for("password_expiry.dashboard"))
