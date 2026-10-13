"""UI модуля уведомлений о сроке паролей AD.

Доступ — только администраторам bAWH (LDAP-группа), без отдельного Basic Auth.
Отчёт — отдельный пункт верхнего меню.
Подключение к каталогу — Настройки → LDAP.
Пороги и получатели — Настройки → Отчёт о паролях.
Почта — Настройки → Почта (SMTP).
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
    get_vacation_check_settings,
    set_ldap_bind_settings,
    set_password_expiry_settings,
    set_vacation_check_settings,
)
from app.services.vacation_imap import VacationImapError, test_imap_connection
from app.services.vacation_password_service import (
    VacationRunInProgressError,
    load_recent_vacation_checks,
    run_vacation_checks,
)
from app.services.report_toggle_service import (
    apply_report_toggle,
    password_reports_status,
)
from app.services.systemd_service import PASSWORD_REPORTS_UNIT

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
        scheduler=password_reports_status(),
        can_toggle=bool(getattr(current_user, "is_admin", False)),
        vacation=get_vacation_check_settings(),
        vacation_checks=load_recent_vacation_checks(),
    )


@bp.route("/toggle", methods=["POST"])
@admin_required
def toggle_schedule():
    """Сверх-тумблер: включить рассылку и службу bawh-password-reports."""
    enabled = request.form.get("schedule_enabled") == "1"

    def _set(value: bool) -> None:
        set_password_expiry_settings(schedule_enabled=value)

    result = apply_report_toggle(
        enabled=enabled,
        set_enabled=_set,
        label="Пароли AD",
        unit=PASSWORD_REPORTS_UNIT,
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
            return _save_report_settings()

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

        if kind == "test_ldap":
            try:
                message = verify_password_directory_access()
            except PasswordAdError as exc:
                flash(str(exc), "danger")
            else:
                flash(message, "success")
            return redirect(url_for("password_expiry.settings_page"))

        flash("Неизвестная форма.", "danger")
        return redirect(url_for("password_expiry.settings_page"))

    ldap = get_ldap_bind_settings()
    return render_template(
        "password_expiry/settings.html",
        ldap=ldap,
    )


def _save_vacation_settings():
    try:
        set_vacation_check_settings(
            schedule_cron=request.form.get("vacation_cron", ""),
            vacation_days=int(request.form.get("vacation_days") or 0),
            imap_host=request.form.get("imap_host", ""),
            imap_port=request.form.get("imap_port", ""),
            imap_use_ssl=request.form.get("imap_use_ssl") == "1",
            imap_user=request.form.get("imap_user", ""),
            imap_password=request.form.get("imap_password"),
            imap_folder=request.form.get("imap_folder", ""),
        )
    except (ValueError, TypeError) as exc:
        flash(str(exc), "danger")
        return redirect(url_for("password_expiry.report_settings"))
    flash("Настройки проверки перед отпуском сохранены.", "success")
    return redirect(url_for("password_expiry.report_settings"))


def _save_report_settings():
    try:
        set_password_expiry_settings(
            max_pwd_age_days=int(request.form.get("max_pwd_age_days") or 0),
            first_warning_days=int(request.form.get("first_warning_days") or 0),
            daily_warning_threshold=int(request.form.get("daily_warning_threshold") or 0),
            instructions_url=request.form.get("instructions_url", ""),
            excluded_ou=request.form.get("excluded_ou", ""),
            search_base=request.form.get("search_base", ""),
            schedule_cron=request.form.get("schedule_cron", ""),
            admin_recipients=request.form.get("admin_recipients", ""),
        )
    except (ValueError, TypeError) as exc:
        flash(str(exc), "danger")
        return redirect(url_for("password_expiry.report_settings"))
    flash("Настройки отчёта о паролях сохранены.", "success")
    return redirect(url_for("password_expiry.report_settings"))


@bp.route("/report-settings", methods=["GET", "POST"])
@admin_required
def report_settings():
    """Пороги, cron и получатели модуля паролей (без LDAP и SMTP)."""
    if request.method == "POST":
        kind = (request.form.get("form") or "").strip()
        if kind == "module":
            return _save_report_settings()
        if kind == "vacation":
            return _save_vacation_settings()
        if kind == "test_imap":
            try:
                message = test_imap_connection(get_vacation_check_settings())
            except VacationImapError as exc:
                flash(str(exc), "danger")
            else:
                flash(message, "success")
            return redirect(url_for("password_expiry.report_settings"))
        flash("Неизвестная форма.", "danger")
        return redirect(url_for("password_expiry.report_settings"))

    return render_template(
        "password_expiry/report_settings.html",
        settings=get_password_expiry_settings(),
        ldap=get_ldap_bind_settings(),
        vacation=get_vacation_check_settings(),
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


@bp.route("/vacation-toggle", methods=["POST"])
@admin_required
def toggle_vacation():
    """Тумблер cron проверки отпусков. Служба bawh-password-reports общая с отчётом."""
    enabled = request.form.get("vacation_enabled") == "1"
    set_vacation_check_settings(enabled=enabled)
    if enabled:
        flash(
            "Проверка паролей перед отпуском включена. "
            "Нужна запущенная служба bawh-password-reports.",
            "success",
        )
    else:
        flash("Проверка паролей перед отпуском выключена.", "success")
    return redirect(url_for("password_expiry.dashboard"))


@bp.route("/vacation-run", methods=["POST"])
@admin_required
def run_vacation_now():
    dry_run = request.form.get("dry_run") == "1"
    try:
        result = run_vacation_checks(
            send_emails=not dry_run,
            mode="dry-run" if dry_run else "web",
        )
    except VacationRunInProgressError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("password_expiry.dashboard"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Отпуска: сбой ручного прогона")
        flash(f"Ошибка проверки отпусков: {exc}", "danger")
        return redirect(url_for("password_expiry.dashboard"))

    if result.exit_code == 0:
        label = "Тестовая проверка отпусков" if dry_run else "Проверка отпусков"
        text = (
            f"{label}: писем {result.messages}, сотрудников {result.employees}, "
            f"пароль не доживёт у {result.risks}, писем сотрудникам {result.notified}."
        )
        if result.error:
            text = f"{text} {result.error}"
        flash(text, "success" if not result.error else "warning")
    else:
        flash(result.error or f"Проверка отпусков завершилась с кодом {result.exit_code}", "danger")
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
