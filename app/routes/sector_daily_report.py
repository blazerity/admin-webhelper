"""UI отчётов о ПК (ноутбуки и СБ по секторам + конфигурации железа).

Доступ — только администраторам. SMTP — общий с модулем паролей AD.
URL prefix: /pc-reports (старый /sector-daily-report редиректит сюда).
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from app.authz import admin_required
from app.services.password_expiry_settings import get_smtp_settings
from app.services.report_toggle_service import apply_report_toggle, pc_reports_status
from app.services.sector_daily_report_service import (
    INVENTORY_STALE_DAYS,
    REPLACEMENT_DISK_GB,
    REPLACEMENT_RAM_GB,
    RunInProgressError,
    build_sector_daily_report,
    charts_payload,
    load_last_run,
    report_to_csv,
    run_sector_daily_report,
)
from app.services.sector_daily_report_settings import (
    get_sector_daily_report_settings,
    set_sector_daily_report_settings,
)
from app.services.systemd_service import (
    PC_REPORTS_UNIT,
    SystemdError,
    ensure_unit_running,
)

logger = logging.getLogger(__name__)

bp = Blueprint("sector_daily_report", __name__, url_prefix="/pc-reports")
legacy_bp = Blueprint("sector_daily_report_legacy", __name__)

MODULE_LABEL = "Отчёты о ПК"


@bp.route("/", methods=["GET"])
@admin_required
def dashboard():
    # Живой снимок парка — диаграммы всегда актуальны без прогона.
    report = build_sector_daily_report(source="live")
    run = load_last_run()
    settings = get_sector_daily_report_settings()
    return render_template(
        "sector_daily_report/dashboard.html",
        report=report,
        charts=charts_payload(report),
        run=run,
        settings=settings,
        smtp=get_smtp_settings(),
        scheduler=pc_reports_status(),
        can_toggle=True,
        replacement_ram_gb=REPLACEMENT_RAM_GB,
        replacement_disk_gb=REPLACEMENT_DISK_GB,
        inventory_stale_days=INVENTORY_STALE_DAYS,
    )


@bp.route("/toggle", methods=["POST"])
@admin_required
def toggle_schedule():
    """Сверх-тумблер: включить рассылку и службу bawh-pc-reports."""
    enabled = request.form.get("schedule_enabled") == "1"

    def _set(value: bool) -> None:
        set_sector_daily_report_settings(schedule_enabled=value)

    result = apply_report_toggle(
        enabled=enabled,
        set_enabled=_set,
        label=MODULE_LABEL,
        unit=PC_REPORTS_UNIT,
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
                ensure = ensure_unit_running(PC_REPORTS_UNIT)
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
        logger.exception("Отчёты о ПК: сбой ручного прогона")
        flash(f"Ошибка прогона: {exc}", "danger")
        return redirect(url_for("sector_daily_report.dashboard"))

    if result.exit_code == 0:
        label = "Тестовый прогон" if dry_run else "Прогон"
        flash(
            f"{label} завершён: день {result.report_date}, "
            f"ПК {result.known_total}, активных {result.active_total}, "
            f"секторов {result.sectors_count}, писем {result.sent_count}.",
            "success",
        )
    else:
        flash(result.error or f"Прогон завершился с кодом {result.exit_code}", "danger")
    return redirect(url_for("sector_daily_report.dashboard"))


@bp.route("/export.csv", methods=["GET"])
@admin_required
def export_csv():
    """Выгрузка парка ПК / кандидатов / пробелов для учёта и закупки."""
    report = build_sector_daily_report(source="export")
    payload = report_to_csv(report)
    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        payload.encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="pc-reports-{stamp}.csv"',
        },
    )


@legacy_bp.route("/sector-daily-report/", defaults={"rest": ""})
@legacy_bp.route("/sector-daily-report/<path:rest>")
@admin_required
def legacy_redirect(rest: str):
    """Старые закладки → /pc-reports/…"""
    target = "/pc-reports/"
    if rest:
        target = f"/pc-reports/{rest}"
    if request.query_string:
        target = f"{target}?{request.query_string.decode()}"
    return redirect(target, code=301)
