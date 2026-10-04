"""UI ежедневного отчёта об активных устройствах по секторам.

Доступ — только администраторам. SMTP — общий с модулем паролей AD.
"""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.authz import admin_required
from app.services.password_expiry_settings import get_smtp_settings
from app.services.report_toggle_service import apply_report_toggle, scheduler_status
from app.services.sector_daily_report_service import (
    RunInProgressError,
    load_last_report,
    load_last_run,
    run_sector_daily_report,
)
from app.services.sector_daily_report_settings import (
    get_sector_daily_report_settings,
    set_sector_daily_report_settings,
)
from app.services.systemd_service import SystemdError, ensure_scheduler_running

logger = logging.getLogger(__name__)

bp = Blueprint("sector_daily_report", __name__, url_prefix="/sector-daily-report")


@bp.route("/", methods=["GET"])
@admin_required
def dashboard():
    report = load_last_report()
    run = load_last_run()
    settings = get_sector_daily_report_settings()
    return render_template(
        "sector_daily_report/dashboard.html",
        report=report,
        run=run,
        settings=settings,
        smtp=get_smtp_settings(),
        scheduler=scheduler_status(),
        can_toggle=True,
    )


@bp.route("/toggle", methods=["POST"])
@admin_required
def toggle_schedule():
    """Сверх-тумблер: включить рассылку отчётов и службу bawh-scheduler."""
    enabled = request.form.get("schedule_enabled") == "1"

    def _set(value: bool) -> None:
        set_sector_daily_report_settings(schedule_enabled=value)

    result = apply_report_toggle(
        enabled=enabled,
        set_enabled=_set,
        label="Отчёт по секторам",
    )
    category = "warning" if "не удалось" in result.message else "success"
    flash(result.message, category)
    return redirect(url_for("sector_daily_report.dashboard"))


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    if request.method == "POST":
        want_enabled = request.form.get("schedule_enabled") == "1"
        try:
            set_sector_daily_report_settings(
                schedule_enabled=want_enabled,
                schedule_cron=request.form.get("schedule_cron", ""),
                recipients=request.form.get("recipients", ""),
            )
        except (ValueError, TypeError) as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sector_daily_report.settings_page"))
        flash("Настройки отчёта сохранены.", "success")
        if want_enabled:
            try:
                ensure = ensure_scheduler_running()
                if ensure.started or ensure.enabled:
                    flash(ensure.message, "info")
            except SystemdError as exc:
                flash(str(exc), "warning")
        return redirect(url_for("sector_daily_report.settings_page"))

    return render_template(
        "sector_daily_report/settings.html",
        settings=get_sector_daily_report_settings(),
        smtp=get_smtp_settings(),
    )


@bp.route("/run", methods=["POST"])
@admin_required
def run_now():
    dry_run = request.form.get("dry_run") == "1"
    try:
        result = run_sector_daily_report(
            send_emails=not dry_run,
            mode="dry-run" if dry_run else "web",
        )
    except RunInProgressError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("sector_daily_report.dashboard"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Отчёт по секторам: сбой ручного прогона")
        flash(f"Ошибка прогона: {exc}", "danger")
        return redirect(url_for("sector_daily_report.dashboard"))

    if result.exit_code == 0:
        label = "Тестовый прогон" if dry_run else "Прогон"
        flash(
            f"{label} завершён: день {result.report_date}, "
            f"активных {result.active_total}, "
            f"секторов {result.sectors_count}, писем {result.sent_count}.",
            "success",
        )
    else:
        flash(result.error or f"Прогон завершился с кодом {result.exit_code}", "danger")
    return redirect(url_for("sector_daily_report.dashboard"))
