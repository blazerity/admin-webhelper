"""Создание и догонка схемы БД из моделей (без Alembic)."""

from __future__ import annotations

import logging

from sqlalchemy import Boolean, Integer, Numeric, String, Text, func, inspect, select, text
from sqlalchemy.sql.schema import Column, DefaultClause

from app.extensions import db
from app.models import ActionKind, seed_action_kinds

logger = logging.getLogger(__name__)

# Одноразовый пересчёт ram_gb/disk_gb после хотфикса номиналов v1.5.1.
HARDWARE_GB_NOMINAL_FIX_KEY = "data_fix.hw_gb_nominal_v151"


def ensure_schema() -> None:
    """Создать недостающие таблицы/колонки и заполнить справочник action_kinds.

    ``create_all`` добавляет только новые таблицы. При обновлении кода на уже
    существующей БД (переустановка ``/opt`` без очистки Postgres) колонки из
    моделей, которых ещё нет в таблице, добавляются отдельно — иначе SELECT
    падает с ``UndefinedColumn`` (например ``users.is_viewer``).

    После колонок — идемпотентные data-fix'ы (флаг в ``app_settings``).
    """
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    if not set(db.metadata.tables).issubset(existing):
        db.create_all()
        inspector = inspect(db.engine)

    _ensure_columns(inspector)
    _migrate_hardware_gb_nominals(inspector)

    count = db.session.scalar(select(func.count()).select_from(ActionKind)) or 0
    if count == 0:
        seed_action_kinds()
        db.session.commit()


def _migrate_hardware_gb_nominals(inspector) -> None:
    """Пересчитать уже записанные ОЗУ/ПЗУ с «рватых» ГиБ на номиналы этикетки.

    Сырых байтов в БД нет: опрос v1.5.0 сохранил round(байты/1024³).
    Повторный WMI не нужен — достаточно normalize_stored_capacity_gb.
    Флаг в app_settings, чтобы не гонять на каждом старте.
    """
    from app.models import AppSetting, Device, DeviceHardwareHistory
    from app.services.hardware_info import normalize_stored_capacity_gb

    tables = set(inspector.get_table_names())
    if "app_settings" not in tables:
        return

    marker = db.session.get(AppSetting, HARDWARE_GB_NOMINAL_FIX_KEY)
    if marker is not None and (marker.value or "").strip() == "1":
        return

    updated = 0
    if "devices" in tables:
        device_cols = {c["name"] for c in inspector.get_columns("devices")}
        if {"ram_gb", "disk_gb"}.issubset(device_cols):
            for device in db.session.scalars(select(Device)).all():
                new_ram = normalize_stored_capacity_gb(device.ram_gb, kind="ram")
                new_disk = normalize_stored_capacity_gb(device.disk_gb, kind="disk")
                if new_ram == device.ram_gb and new_disk == device.disk_gb:
                    continue
                device.ram_gb = new_ram
                device.disk_gb = new_disk
                updated += 1

    if "device_hardware_history" in tables:
        history_cols = {c["name"] for c in inspector.get_columns("device_hardware_history")}
        if {"ram_gb", "disk_gb"}.issubset(history_cols):
            for row in db.session.scalars(select(DeviceHardwareHistory)).all():
                new_ram = normalize_stored_capacity_gb(row.ram_gb, kind="ram")
                new_disk = normalize_stored_capacity_gb(row.disk_gb, kind="disk")
                if new_ram == row.ram_gb and new_disk == row.disk_gb:
                    continue
                row.ram_gb = new_ram
                row.disk_gb = new_disk
                updated += 1

    if marker is None:
        db.session.add(AppSetting(key=HARDWARE_GB_NOMINAL_FIX_KEY, value="1"))
    else:
        marker.value = "1"
    db.session.commit()
    logger.info(
        "ensure_schema: hardware GB nominal fix v1.5.1 — updated %s rows",
        updated,
    )


def _ensure_columns(inspector) -> None:
    """Добавить колонки из моделей, которых ещё нет в существующих таблицах."""
    dialect = db.engine.dialect
    added: list[str] = []
    with db.engine.begin() as conn:
        table_names = set(inspector.get_table_names())
        for table_name, table in db.metadata.tables.items():
            if table_name not in table_names:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                ddl = _add_column_ddl(table_name, column, dialect)
                conn.execute(text(ddl))
                added.append(f"{table_name}.{column.name}")
    if added:
        logger.info("ensure_schema: added columns: %s", ", ".join(added))


def _quote_ident(name: str, dialect) -> str:
    return dialect.identifier_preparer.quote(name)


def _add_column_ddl(table_name: str, column: Column, dialect) -> str:
    type_sql = column.type.compile(dialect=dialect)
    table_q = _quote_ident(table_name, dialect)
    col_q = _quote_ident(column.name, dialect)
    pieces = [f"ALTER TABLE {table_q} ADD COLUMN {col_q} {type_sql}"]

    default_sql = _column_default_sql(column, dialect)
    nullable = column.nullable
    if default_sql is not None:
        pieces.append(f"DEFAULT {default_sql}")
    elif not nullable:
        # NOT NULL без DEFAULT не встанет на непустой таблице — оставляем NULL.
        nullable = True
        logger.warning(
            "ensure_schema: %s.%s added as NULL (нет DEFAULT для NOT NULL)",
            table_name,
            column.name,
        )

    if not nullable:
        pieces.append("NOT NULL")

    return " ".join(pieces)


def _column_default_sql(column: Column, dialect) -> str | None:
    """DEFAULT для ADD COLUMN: server_default или скалярный Python default."""
    if column.server_default is not None:
        rendered = _render_server_default(column.server_default, column, dialect)
        if rendered is not None:
            return rendered

    if column.default is not None and getattr(column.default, "is_scalar", False):
        return _literal_sql(column.default.arg, column, dialect)

    if not column.nullable:
        fallback = _type_fallback_default(column)
        if fallback is not None:
            return _literal_sql(fallback, column, dialect)

    return None


def _render_server_default(
    server_default: DefaultClause, column: Column, dialect
) -> str | None:
    arg = getattr(server_default, "arg", None)
    if arg is None:
        return None
    if hasattr(arg, "text"):
        return _normalize_default_text(str(arg.text), column, dialect)
    if isinstance(arg, str):
        return _normalize_default_text(arg, column, dialect)
    if isinstance(arg, (bool, int, float)):
        return _literal_sql(arg, column, dialect)
    # sqlalchemy.true() / false() и прочие ClauseElement — компилируем в диалект.
    compile_ = getattr(arg, "compile", None)
    if callable(compile_):
        try:
            return str(compile_(dialect=dialect))
        except Exception:
            return None
    return None


def _normalize_default_text(raw: str, column: Column, dialect) -> str:
    """Привести литерал DEFAULT к типу колонки и диалекту.

    В моделях часто ``server_default="0"``/``"1"`` (удобно для SQLite), но
    PostgreSQL для BOOLEAN требует ``true``/``false``, не integer.
    """
    value = raw.strip()
    if isinstance(column.type, Boolean):
        parsed = _parse_boolish(value)
        if parsed is not None:
            return _bool_sql(parsed, dialect)
    if value.upper() in {"TRUE", "FALSE", "NULL"}:
        return value.lower() if dialect.name != "sqlite" else ("1" if value.upper() == "TRUE" else "0" if value.upper() == "FALSE" else "NULL")
    if value[:1].isdigit() or value[:1] in "-+":
        return value
    # Строковый литерал без кавычек в server_default (как run_as='psexec').
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value
    return _literal_sql(value, column, dialect)


def _parse_boolish(value: str) -> bool | None:
    lowered = value.strip().strip("'\"").lower()
    if lowered in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "f", "no", "n", "off"}:
        return False
    return None


def _type_fallback_default(column: Column):
    coltype = column.type
    if isinstance(coltype, Boolean):
        return False
    if isinstance(coltype, Integer):
        return 0
    if isinstance(coltype, Numeric):
        return 0
    if isinstance(coltype, (String, Text)):
        return ""
    return None


def _literal_sql(value, column: Column, dialect) -> str:
    if isinstance(column.type, Boolean):
        if isinstance(value, str):
            parsed = _parse_boolish(value)
            if parsed is None:
                raise TypeError(f"Не могу разобрать boolean DEFAULT {value!r}")
            return _bool_sql(parsed, dialect)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _bool_sql(bool(value), dialect)
        return _bool_sql(bool(value), dialect)
    if isinstance(value, bool):
        return _bool_sql(value, dialect)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"Не могу сформировать DEFAULT для значения типа {type(value)!r}")


def _bool_sql(value: bool, dialect) -> str:
    # SQLite хранит boolean как integer; PostgreSQL — native boolean.
    if dialect.name == "sqlite":
        return "1" if value else "0"
    return "true" if value else "false"
