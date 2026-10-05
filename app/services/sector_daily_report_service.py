"""Отчёты о ПК: ноутбуки и СБ по секторам + конфигурации железа.

Дашборд и письмо строятся из текущего парка (devices) и активности
за прошедший локальный календарный день (device_history = online).
Тип ПК — hostname (n… → ноутбук, w… → СБ), см. device_kind.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from flask import render_template
from sqlalchemy import select

from app.extensions import db
from app.models import Device, DeviceHistory, DeviceStatus, Sector, SectorDailyReportRun
from app.services.device_kind import (
    KIND_DESKTOP,
    KIND_NOTEBOOK,
    classify_device_kind,
    kind_short_label,
)
from app.services.hardware_info import format_os_label
from app.services.password_expiry_settings import get_smtp_settings
from app.services.password_mailer import PasswordMailer, PasswordMailerError
from app.services.sector_daily_report_settings import get_sector_daily_report_settings
from app.utils import as_utc, utcnow

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()

PC_KINDS = frozenset({KIND_NOTEBOOK, KIND_DESKTOP})

# Пороги «кандидат на замену» — ориентир для сисадминов / закупки.
REPLACEMENT_RAM_GB = 8
REPLACEMENT_DISK_GB = 256
INVENTORY_STALE_DAYS = 14
TOP_CPU_LIMIT = 10


class RunInProgressError(RuntimeError):
    """Уже выполняется другой прогон."""


@dataclass
class NamedCount:
    label: str
    count: int


@dataclass
class ChartSeries:
    labels: list[str] = field(default_factory=list)
    values: list[int] = field(default_factory=list)


@dataclass
class SectorPcRow:
    sector_id: int
    sector_name: str
    notebook_count: int = 0
    desktop_count: int = 0
    pc_total: int = 0
    active_notebook: int = 0
    active_desktop: int = 0
    active_pc: int = 0
    with_hardware: int = 0
    without_hardware: int = 0
    avg_ram_gb: float | None = None
    avg_disk_gb: float | None = None
    # Совместимость со старым JSON / шаблонами письма.
    active_count: int = 0
    known_count: int = 0


@dataclass
class PcDeviceBrief:
    device_id: int
    hostname: str
    ip: str
    sector_name: str
    kind: str
    kind_label: str
    cpu_name: str | None = None
    ram_gb: int | None = None
    disk_gb: int | None = None
    os_label: str | None = None
    hardware_checked_at: str | None = None
    reason: str = ""


@dataclass
class StoredReport:
    report_date: str
    generated_at: str
    timezone: str
    sectors: list[SectorPcRow] = field(default_factory=list)
    # Итоги парка ПК (ноутбук + СБ).
    notebook_total: int = 0
    desktop_total: int = 0
    pc_total: int = 0
    active_pc_total: int = 0
    with_hardware_total: int = 0
    without_hardware_total: int = 0
    replacement_total: int = 0
    inventory_gap_total: int = 0
    # Диаграммы (JSON → Chart.js).
    kind_chart: ChartSeries = field(default_factory=ChartSeries)
    sector_pc_chart: ChartSeries = field(default_factory=ChartSeries)
    sector_notebook_chart: ChartSeries = field(default_factory=ChartSeries)
    sector_desktop_chart: ChartSeries = field(default_factory=ChartSeries)
    ram_chart: ChartSeries = field(default_factory=ChartSeries)
    disk_chart: ChartSeries = field(default_factory=ChartSeries)
    os_chart: ChartSeries = field(default_factory=ChartSeries)
    cpu_chart: ChartSeries = field(default_factory=ChartSeries)
    # Функции для сисадминов.
    replacement_candidates: list[PcDeviceBrief] = field(default_factory=list)
    inventory_gaps: list[PcDeviceBrief] = field(default_factory=list)
    source: str = "run"
    # Совместимость с прежним RunResult / UI flash.
    active_total: int = 0
    known_total: int = 0

    @property
    def sectors_count(self) -> int:
        return len(self.sectors)


@dataclass
class RunResult:
    exit_code: int
    report_date: str = ""
    active_total: int = 0
    known_total: int = 0
    sectors_count: int = 0
    sent_count: int = 0
    error: str = ""
    report: StoredReport | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def previous_calendar_day(today: date | None = None) -> date:
    """Прошедший календарный день в локальном TZ сервера."""
    if today is not None:
        return today
    local_tz = _local_tzinfo()
    return datetime.now(local_tz).date() - timedelta(days=1)


def day_window_utc(report_date: date) -> tuple[datetime, datetime]:
    """Полуночный интервал [start, end) отчётной даты → UTC для запросов к БД."""
    local_tz = _local_tzinfo()
    start_local = datetime.combine(report_date, time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _device_kind(device: Device) -> str:
    return classify_device_kind(
        device.hostname,
        serial_number=device.serial_number,
        mac=device.mac,
        fingerprint_kind=device.fingerprint_kind,
    )


def _is_pc(kind: str) -> bool:
    return kind in PC_KINDS


def _os_label_for(device: Device) -> str | None:
    label = format_os_label(device.os_family, device.os_edition, device.os_display_version)
    return label or None


def _short_cpu(name: str | None) -> str:
    """Укоротить длинное имя CPU для диаграмм."""
    text = " ".join(str(name or "").split()).strip()
    if not text:
        return "Неизвестно"
    for prefix in (
        "Intel(R) Core(TM) ",
        "Intel(R) Xeon(R) ",
        "Intel(R) Pentium(R) ",
        "Intel(R) Celeron(R) ",
        "AMD Ryzen ",
        "AMD ",
        "Intel ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.replace(" CPU @", " @").replace("(R)", "").replace("(TM)", "")
    if len(text) > 42:
        text = text[:39] + "…"
    return text or "Неизвестно"


def _ram_bucket(ram_gb: int | None) -> str | None:
    if ram_gb is None:
        return None
    if ram_gb <= 4:
        return "≤ 4 ГБ"
    if ram_gb <= 8:
        return "5–8 ГБ"
    if ram_gb <= 16:
        return "9–16 ГБ"
    if ram_gb <= 32:
        return "17–32 ГБ"
    return "> 32 ГБ"


def _disk_bucket(disk_gb: int | None) -> str | None:
    if disk_gb is None:
        return None
    if disk_gb <= 128:
        return "≤ 128 ГБ"
    if disk_gb <= 256:
        return "129–256 ГБ"
    if disk_gb <= 512:
        return "257–512 ГБ"
    if disk_gb <= 1024:
        return "513–1024 ГБ"
    return "> 1 ТБ"


def _series_from_counter(counter: Counter[str], *, order: Iterable[str] | None = None) -> ChartSeries:
    if order is not None:
        labels = [label for label in order if counter.get(label)]
        # Хвост неизвестных ключей — в конце.
        labels.extend(sorted(k for k in counter if k not in labels))
    else:
        labels = [label for label, _ in counter.most_common()]
    return ChartSeries(labels=labels, values=[int(counter[label]) for label in labels])


def _replacement_reasons(device: Device) -> list[str]:
    reasons: list[str] = []
    if device.ram_gb is not None and device.ram_gb <= REPLACEMENT_RAM_GB:
        reasons.append(f"ОЗУ {device.ram_gb} ГБ")
    if device.disk_gb is not None and device.disk_gb <= REPLACEMENT_DISK_GB:
        reasons.append(f"диск {device.disk_gb} ГБ")
    family = (device.os_family or "").strip().lower()
    if "windows 10" in family or family == "10" or family.endswith(" 10"):
        reasons.append("Windows 10")
    elif family and "windows 11" not in family and "server" not in family:
        # Нестандартная/старая клиентская ОС при наличии снимка.
        if device.hardware_checked_at and "windows" in family:
            reasons.append(format_os_label(device.os_family, device.os_edition, device.os_display_version) or "старая ОС")
    return reasons


def _inventory_gap_reason(device: Device, *, now: datetime, stale_before: datetime) -> str | None:
    checked = as_utc(device.hardware_checked_at) if device.hardware_checked_at else None
    if checked is None:
        return "нет снимка железа"
    if checked < stale_before:
        age_days = max(1, int((now - checked).total_seconds() // 86400))
        return f"снимок старше {age_days} дн."
    if not device.cpu_name and device.ram_gb is None and device.disk_gb is None:
        return "снимок пустой"
    return None


def _brief(
    device: Device,
    *,
    kind: str,
    sector_name: str,
    reason: str,
) -> PcDeviceBrief:
    checked = ""
    if device.hardware_checked_at:
        checked = as_utc(device.hardware_checked_at).astimezone().replace(microsecond=0).isoformat()
    return PcDeviceBrief(
        device_id=device.id,
        hostname=(device.hostname or "").strip() or "—",
        ip=device.ip,
        sector_name=sector_name,
        kind=kind,
        kind_label=kind_short_label(kind),
        cpu_name=device.cpu_name,
        ram_gb=device.ram_gb,
        disk_gb=device.disk_gb,
        os_label=_os_label_for(device),
        hardware_checked_at=checked or None,
        reason=reason,
    )


def build_sector_daily_report(
    *,
    report_date: date | None = None,
    source: str = "run",
) -> StoredReport:
    """Снимок парка ПК: количества, железо, кандидаты, пробелы инвентаризации."""
    day = previous_calendar_day(report_date)
    start_utc, end_utc = day_window_utc(day)
    local_tz = _local_tzinfo()
    tz_name = getattr(local_tz, "key", None) or str(local_tz)
    now = utcnow()
    stale_before = now - timedelta(days=INVENTORY_STALE_DAYS)

    sectors = list(db.session.scalars(select(Sector).order_by(Sector.name.asc())).all())
    sector_name = {sector.id: sector.name for sector in sectors}

    devices = list(db.session.scalars(select(Device)).all())
    pcs: list[tuple[Device, str]] = []
    for device in devices:
        kind = _device_kind(device)
        if _is_pc(kind):
            pcs.append((device, kind))

    pc_ids = {device.id for device, _ in pcs}
    active_ids: set[int] = set()
    if pc_ids:
        active_rows = db.session.execute(
            select(DeviceHistory.device_id)
            .where(
                DeviceHistory.device_id.in_(pc_ids),
                DeviceHistory.status == DeviceStatus.ONLINE,
                DeviceHistory.timestamp >= start_utc,
                DeviceHistory.timestamp < end_utc,
            )
            .distinct()
        ).all()
        active_ids = {int(row[0]) for row in active_rows}

    # Агрегаты по секторам.
    by_sector: dict[int, dict[str, Any]] = {
        sector.id: {
            "notebook": 0,
            "desktop": 0,
            "active_notebook": 0,
            "active_desktop": 0,
            "with_hw": 0,
            "without_hw": 0,
            "ram_sum": 0,
            "ram_n": 0,
            "disk_sum": 0,
            "disk_n": 0,
        }
        for sector in sectors
    }

    ram_counter: Counter[str] = Counter()
    disk_counter: Counter[str] = Counter()
    os_counter: Counter[str] = Counter()
    cpu_counter: Counter[str] = Counter()
    replacement: list[PcDeviceBrief] = []
    gaps: list[PcDeviceBrief] = []

    notebook_total = 0
    desktop_total = 0
    with_hw_total = 0
    without_hw_total = 0

    for device, kind in pcs:
        bucket = by_sector.get(device.sector_id)
        if bucket is None:
            # Устройство без известного сектора — пропускаем агрегаты таблицы.
            bucket = None
        name = sector_name.get(device.sector_id, "—")
        is_active = device.id in active_ids

        if kind == KIND_NOTEBOOK:
            notebook_total += 1
            if bucket is not None:
                bucket["notebook"] += 1
                if is_active:
                    bucket["active_notebook"] += 1
        else:
            desktop_total += 1
            if bucket is not None:
                bucket["desktop"] += 1
                if is_active:
                    bucket["active_desktop"] += 1

        has_hw = device.hardware_checked_at is not None and (
            device.cpu_name or device.ram_gb is not None or device.disk_gb is not None or device.os_family
        )
        if has_hw:
            with_hw_total += 1
            if bucket is not None:
                bucket["with_hw"] += 1
            ram_label = _ram_bucket(device.ram_gb)
            if ram_label:
                ram_counter[ram_label] += 1
                if bucket is not None and device.ram_gb is not None:
                    bucket["ram_sum"] += device.ram_gb
                    bucket["ram_n"] += 1
            disk_label = _disk_bucket(device.disk_gb)
            if disk_label:
                disk_counter[disk_label] += 1
                if bucket is not None and device.disk_gb is not None:
                    bucket["disk_sum"] += device.disk_gb
                    bucket["disk_n"] += 1
            os_label = _os_label_for(device) or "ОС не распознана"
            os_counter[os_label] += 1
            cpu_counter[_short_cpu(device.cpu_name)] += 1
        else:
            without_hw_total += 1
            if bucket is not None:
                bucket["without_hw"] += 1

        reasons = _replacement_reasons(device) if has_hw else []
        if reasons:
            replacement.append(_brief(device, kind=kind, sector_name=name, reason="; ".join(reasons)))

        gap = _inventory_gap_reason(device, now=now, stale_before=stale_before)
        if gap:
            gaps.append(_brief(device, kind=kind, sector_name=name, reason=gap))

    replacement.sort(key=lambda item: (item.sector_name, item.hostname))
    gaps.sort(key=lambda item: (item.sector_name, item.hostname))

    rows: list[SectorPcRow] = []
    active_pc_total = 0
    for sector in sectors:
        data = by_sector[sector.id]
        pc_total = data["notebook"] + data["desktop"]
        active_pc = data["active_notebook"] + data["active_desktop"]
        active_pc_total += active_pc
        avg_ram = round(data["ram_sum"] / data["ram_n"], 1) if data["ram_n"] else None
        avg_disk = round(data["disk_sum"] / data["disk_n"], 1) if data["disk_n"] else None
        rows.append(
            SectorPcRow(
                sector_id=sector.id,
                sector_name=sector.name,
                notebook_count=data["notebook"],
                desktop_count=data["desktop"],
                pc_total=pc_total,
                active_notebook=data["active_notebook"],
                active_desktop=data["active_desktop"],
                active_pc=active_pc,
                with_hardware=data["with_hw"],
                without_hardware=data["without_hw"],
                avg_ram_gb=avg_ram,
                avg_disk_gb=avg_disk,
                active_count=active_pc,
                known_count=pc_total,
            )
        )

    pc_total = notebook_total + desktop_total
    sectors_with_pc = [row for row in rows if row.pc_total > 0]

    ram_order = ("≤ 4 ГБ", "5–8 ГБ", "9–16 ГБ", "17–32 ГБ", "> 32 ГБ")
    disk_order = ("≤ 128 ГБ", "129–256 ГБ", "257–512 ГБ", "513–1024 ГБ", "> 1 ТБ")

    kind_chart = ChartSeries(
        labels=[kind_short_label(KIND_NOTEBOOK), kind_short_label(KIND_DESKTOP)],
        values=[notebook_total, desktop_total],
    )
    sector_pc_chart = ChartSeries(
        labels=[row.sector_name for row in sectors_with_pc],
        values=[row.pc_total for row in sectors_with_pc],
    )
    sector_notebook_chart = ChartSeries(
        labels=[row.sector_name for row in sectors_with_pc],
        values=[row.notebook_count for row in sectors_with_pc],
    )
    sector_desktop_chart = ChartSeries(
        labels=[row.sector_name for row in sectors_with_pc],
        values=[row.desktop_count for row in sectors_with_pc],
    )
    cpu_top = cpu_counter.most_common(TOP_CPU_LIMIT)

    return StoredReport(
        report_date=day.isoformat(),
        generated_at=_iso_now(),
        timezone=tz_name,
        sectors=rows,
        notebook_total=notebook_total,
        desktop_total=desktop_total,
        pc_total=pc_total,
        active_pc_total=active_pc_total,
        with_hardware_total=with_hw_total,
        without_hardware_total=without_hw_total,
        replacement_total=len(replacement),
        inventory_gap_total=len(gaps),
        kind_chart=kind_chart,
        sector_pc_chart=sector_pc_chart,
        sector_notebook_chart=sector_notebook_chart,
        sector_desktop_chart=sector_desktop_chart,
        ram_chart=_series_from_counter(ram_counter, order=ram_order),
        disk_chart=_series_from_counter(disk_counter, order=disk_order),
        os_chart=_series_from_counter(os_counter),
        cpu_chart=ChartSeries(labels=[name for name, _ in cpu_top], values=[n for _, n in cpu_top]),
        replacement_candidates=replacement,
        inventory_gaps=gaps,
        source=source,
        active_total=active_pc_total,
        known_total=pc_total,
    )


def charts_payload(report: StoredReport) -> dict[str, dict[str, list]]:
    """Словари для Chart.js (labels/values) — без dataclass в шаблоне."""
    return {
        "kind": asdict(report.kind_chart),
        "sectors": asdict(report.sector_pc_chart),
        "notebooks": asdict(report.sector_notebook_chart),
        "desktops": asdict(report.sector_desktop_chart),
        "ram": asdict(report.ram_chart),
        "disk": asdict(report.disk_chart),
        "os": asdict(report.os_chart),
        "cpu": asdict(report.cpu_chart),
    }


def report_to_csv(report: StoredReport) -> str:
    """CSV парка ПК по секторам + кандидаты/пробелы — для закупки и учёта."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["# Отчёты о ПК", report.report_date, report.generated_at])
    writer.writerow([])
    writer.writerow(
        [
            "Сектор",
            "Ноутбуки",
            "СБ",
            "ПК всего",
            "Активных вчера",
            "Со снимком железа",
            "Без снимка",
            "Среднее ОЗУ ГБ",
            "Средний диск ГБ",
        ]
    )
    for row in report.sectors:
        writer.writerow(
            [
                row.sector_name,
                row.notebook_count,
                row.desktop_count,
                row.pc_total,
                row.active_pc,
                row.with_hardware,
                row.without_hardware,
                row.avg_ram_gb if row.avg_ram_gb is not None else "",
                row.avg_disk_gb if row.avg_disk_gb is not None else "",
            ]
        )
    writer.writerow([])
    writer.writerow(["# Кандидаты на замену"])
    writer.writerow(
        ["Сектор", "Тип", "Имя", "IP", "CPU", "ОЗУ ГБ", "Диск ГБ", "ОС", "Причина"]
    )
    for item in report.replacement_candidates:
        writer.writerow(
            [
                item.sector_name,
                item.kind_label,
                item.hostname,
                item.ip,
                item.cpu_name or "",
                item.ram_gb if item.ram_gb is not None else "",
                item.disk_gb if item.disk_gb is not None else "",
                item.os_label or "",
                item.reason,
            ]
        )
    writer.writerow([])
    writer.writerow(["# Пробелы инвентаризации"])
    writer.writerow(["Сектор", "Тип", "Имя", "IP", "Причина", "Снято"])
    for item in report.inventory_gaps:
        writer.writerow(
            [
                item.sector_name,
                item.kind_label,
                item.hostname,
                item.ip,
                item.reason,
                item.hardware_checked_at or "",
            ]
        )
    return buf.getvalue()


def _chart_from_raw(raw: Any) -> ChartSeries:
    if not isinstance(raw, dict):
        return ChartSeries()
    labels = [str(item) for item in (raw.get("labels") or [])]
    values = [int(item or 0) for item in (raw.get("values") or [])]
    return ChartSeries(labels=labels, values=values)


def _sector_from_raw(item: dict[str, Any]) -> SectorPcRow:
    pc_total = int(item.get("pc_total") or item.get("known_count") or 0)
    active_pc = int(item.get("active_pc") or item.get("active_count") or 0)
    return SectorPcRow(
        sector_id=int(item.get("sector_id") or 0),
        sector_name=str(item.get("sector_name") or ""),
        notebook_count=int(item.get("notebook_count") or 0),
        desktop_count=int(item.get("desktop_count") or 0),
        pc_total=pc_total,
        active_notebook=int(item.get("active_notebook") or 0),
        active_desktop=int(item.get("active_desktop") or 0),
        active_pc=active_pc,
        with_hardware=int(item.get("with_hardware") or 0),
        without_hardware=int(item.get("without_hardware") or 0),
        avg_ram_gb=item.get("avg_ram_gb"),
        avg_disk_gb=item.get("avg_disk_gb"),
        active_count=active_pc,
        known_count=pc_total,
    )


def _brief_from_raw(item: dict[str, Any]) -> PcDeviceBrief:
    return PcDeviceBrief(
        device_id=int(item.get("device_id") or 0),
        hostname=str(item.get("hostname") or "—"),
        ip=str(item.get("ip") or ""),
        sector_name=str(item.get("sector_name") or ""),
        kind=str(item.get("kind") or ""),
        kind_label=str(item.get("kind_label") or ""),
        cpu_name=item.get("cpu_name"),
        ram_gb=item.get("ram_gb"),
        disk_gb=item.get("disk_gb"),
        os_label=item.get("os_label"),
        hardware_checked_at=item.get("hardware_checked_at"),
        reason=str(item.get("reason") or ""),
    )


def _persist_run(
    result: RunResult,
    *,
    send_emails: bool,
    mode: str,
    started_at: datetime,
    report: StoredReport | None,
) -> None:
    report_day = date.fromisoformat(result.report_date) if result.report_date else previous_calendar_day()
    row = SectorDailyReportRun(
        started_at=started_at,
        finished_at=utcnow(),
        exit_code=result.exit_code,
        send_emails=send_emails,
        report_date=report_day,
        active_total=result.active_total,
        known_total=result.known_total,
        sectors_count=result.sectors_count,
        sent_count=result.sent_count,
        error=result.error or "",
        mode=mode,
        report_json=json.dumps(asdict(report), ensure_ascii=False) if report else "",
    )
    db.session.add(row)
    db.session.commit()


def load_last_run() -> SectorDailyReportRun | None:
    return db.session.scalars(
        select(SectorDailyReportRun).order_by(SectorDailyReportRun.id.desc()).limit(1)
    ).first()


def load_last_report() -> StoredReport | None:
    run = load_last_run()
    if run is None or not run.report_json:
        return None
    try:
        raw: dict[str, Any] = json.loads(run.report_json)
    except json.JSONDecodeError:
        return None
    sectors = [_sector_from_raw(item) for item in raw.get("sectors") or [] if isinstance(item, dict)]
    replacements = [
        _brief_from_raw(item)
        for item in raw.get("replacement_candidates") or []
        if isinstance(item, dict)
    ]
    gaps = [
        _brief_from_raw(item)
        for item in raw.get("inventory_gaps") or []
        if isinstance(item, dict)
    ]
    pc_total = int(raw.get("pc_total") or raw.get("known_total") or 0)
    active_pc = int(raw.get("active_pc_total") or raw.get("active_total") or 0)
    return StoredReport(
        report_date=str(raw.get("report_date") or ""),
        generated_at=str(raw.get("generated_at") or ""),
        timezone=str(raw.get("timezone") or ""),
        sectors=sectors,
        notebook_total=int(raw.get("notebook_total") or 0),
        desktop_total=int(raw.get("desktop_total") or 0),
        pc_total=pc_total,
        active_pc_total=active_pc,
        with_hardware_total=int(raw.get("with_hardware_total") or 0),
        without_hardware_total=int(raw.get("without_hardware_total") or 0),
        replacement_total=int(raw.get("replacement_total") or len(replacements)),
        inventory_gap_total=int(raw.get("inventory_gap_total") or len(gaps)),
        kind_chart=_chart_from_raw(raw.get("kind_chart")),
        sector_pc_chart=_chart_from_raw(raw.get("sector_pc_chart")),
        sector_notebook_chart=_chart_from_raw(raw.get("sector_notebook_chart")),
        sector_desktop_chart=_chart_from_raw(raw.get("sector_desktop_chart")),
        ram_chart=_chart_from_raw(raw.get("ram_chart")),
        disk_chart=_chart_from_raw(raw.get("disk_chart")),
        os_chart=_chart_from_raw(raw.get("os_chart")),
        cpu_chart=_chart_from_raw(raw.get("cpu_chart")),
        replacement_candidates=replacements,
        inventory_gaps=gaps,
        source=str(raw.get("source") or "run"),
        active_total=active_pc,
        known_total=pc_total,
    )


def run_sector_daily_report(
    *,
    send_emails: bool = True,
    mode: str = "scheduled",
    report_date: date | None = None,
) -> RunResult:
    """Полный прогон. Без параллельных запусков в одном процессе."""
    if not _run_lock.acquire(blocking=False):
        raise RunInProgressError("Прогон уже выполняется")
    started = utcnow()
    try:
        result = _run_unlocked(
            send_emails=send_emails,
            mode=mode,
            report_date=report_date,
        )
        _persist_run(
            result,
            send_emails=send_emails,
            mode=mode,
            started_at=started,
            report=result.report,
        )
        return result
    except Exception:
        db.session.rollback()
        raise
    finally:
        _run_lock.release()


def _run_unlocked(
    *,
    send_emails: bool,
    mode: str,
    report_date: date | None,
) -> RunResult:
    settings = get_sector_daily_report_settings()
    mailer = PasswordMailer(get_smtp_settings(), dry_run=not send_emails)

    if not send_emails:
        logger.info("Отчёты о ПК: dry-run — SMTP не используется")

    try:
        report = build_sector_daily_report(report_date=report_date, source=mode)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Отчёты о ПК: сбой построения")
        return RunResult(exit_code=1, error=str(exc))

    sent = 0
    if settings.recipients:
        try:
            html = render_template(
                "email/sector_daily_report.html",
                report=report,
                report_date=report.report_date,
                sectors=report.sectors,
                active_total=report.active_pc_total,
                known_total=report.pc_total,
                notebook_total=report.notebook_total,
                desktop_total=report.desktop_total,
                with_hardware_total=report.with_hardware_total,
                without_hardware_total=report.without_hardware_total,
                replacement_total=report.replacement_total,
                inventory_gap_total=report.inventory_gap_total,
                timezone=report.timezone,
            )
            mailer.send_html(
                settings.recipients,
                f"Отчёты о ПК — {report.report_date}",
                html,
            )
            sent = 1 if send_emails else 0
        except PasswordMailerError as exc:
            logger.exception("Отчёты о ПК: не удалось отправить письмо")
            return RunResult(
                exit_code=1,
                report_date=report.report_date,
                active_total=report.active_total,
                known_total=report.known_total,
                sectors_count=report.sectors_count,
                sent_count=0,
                error=str(exc),
                report=report,
            )
    else:
        logger.info("Отчёты о ПК: получатели не заданы — письмо не отправлено")

    logger.info(
        "Отчёты о ПК: готово date=%s pc=%s notebooks=%s desktops=%s active=%s sent=%s",
        report.report_date,
        report.pc_total,
        report.notebook_total,
        report.desktop_total,
        report.active_pc_total,
        sent,
    )
    return RunResult(
        exit_code=0,
        report_date=report.report_date,
        active_total=report.active_total,
        known_total=report.known_total,
        sectors_count=report.sectors_count,
        sent_count=sent,
        report=report,
    )
