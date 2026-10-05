"""Создание и догонка схемы БД из моделей (без Alembic)."""

from __future__ import annotations

import logging

from sqlalchemy import Boolean, Integer, Numeric, String, Text, func, inspect, select, text
from sqlalchemy.sql.schema import Column, DefaultClause

from app.extensions import db
from app.models import ActionKind, seed_action_kinds

logger = logging.getLogger(__name__)


def ensure_schema() -> None:
    """Создать недостающие таблицы/колонки и заполнить справочник action_kinds.

    ``create_all`` добавляет только новые таблицы. При обновлении кода на уже
    существующей БД (переустановка ``/opt`` без очистки Postgres) колонки из
    моделей, которых ещё нет в таблице, добавляются отдельно — иначе SELECT
    падает с ``UndefinedColumn`` (например ``users.is_viewer``).
    """
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    if not set(db.metadata.tables).issubset(existing):
        db.create_all()
        inspector = inspect(db.engine)

    _ensure_columns(inspector)

    count = db.session.scalar(select(func.count()).select_from(ActionKind)) or 0
    if count == 0:
        seed_action_kinds()
        db.session.commit()


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
        rendered = _render_server_default(column.server_default, dialect)
        if rendered is not None:
            return rendered

    if column.default is not None and getattr(column.default, "is_scalar", False):
        return _literal_sql(column.default.arg, dialect)

    if not column.nullable:
        fallback = _type_fallback_default(column)
        if fallback is not None:
            return _literal_sql(fallback, dialect)

    return None


def _render_server_default(server_default: DefaultClause, dialect) -> str | None:
    arg = getattr(server_default, "arg", None)
    if arg is None:
        return None
    if hasattr(arg, "text"):
        return str(arg.text)
    if isinstance(arg, str):
        # Как у Script.is_published (server_default="1") — уже SQL-литерал.
        if arg.upper() in {"TRUE", "FALSE", "NULL"} or arg[:1].isdigit() or arg[:1] in "-+":
            return arg
        return _literal_sql(arg, dialect)
    if isinstance(arg, (bool, int, float)):
        return _literal_sql(arg, dialect)
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


def _literal_sql(value, dialect) -> str:
    if isinstance(value, bool):
        if dialect.name == "sqlite":
            return "1" if value else "0"
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"Не могу сформировать DEFAULT для значения типа {type(value)!r}")
