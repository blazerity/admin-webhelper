"""Справочник УЗ на ПК и журнал появлений УЗ на устройствах.

Revision ID: 0005_accounts_actions
Revises: 0004_device_serial
Create Date: 2026-10-02

Вариант B:
* endpoint_accounts — отдельный справочник учёток на конечных точках
  (не путать с users — операторами сайта);
* device_account_history — факты «УЗ замечена на устройстве»;
* devices.current_account_id — кто сейчас сидит за ПК (по последнему опросу);
* action_kinds — справочник типов действий в системе.
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_accounts_actions"
down_revision = "0004_device_serial"
branch_labels = None
depends_on = None

_ACTION_KIND_SEED = (
    ("poll", "Опрос доступности", "Фоновый ICMP-опрос сектора"),
    ("ping", "Ping", "Ручная проверка доступности с карточки"),
    ("tracert", "Трассировка", "Трассировка маршрута до устройства"),
    ("command", "Команда", "Произвольная команда через PsExec"),
    ("script", "Скрипт", "Запуск скрипта из библиотеки"),
    ("account_sighting", "Обнаружение УЗ", "Учётка замечена на устройстве"),
)


def upgrade():
    op.create_table(
        "endpoint_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("domain", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("ldap_dn", sa.String(length=512), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("domain", "username", name="uq_endpoint_accounts_domain_user"),
    )
    op.create_index("ix_endpoint_accounts_username", "endpoint_accounts", ["username"])
    op.create_index("ix_endpoint_accounts_domain", "endpoint_accounts", ["domain"])

    op.create_table(
        "action_kinds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("code", name="uq_action_kinds_code"),
    )
    op.create_index("ix_action_kinds_code", "action_kinds", ["code"])

    action_kinds = sa.table(
        "action_kinds",
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        action_kinds,
        [
            {"code": code, "title": title, "description": description}
            for code, title, description in _ACTION_KIND_SEED
        ],
    )

    op.add_column(
        "devices",
        sa.Column("current_account_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("current_account_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_devices_current_account_id", "devices", ["current_account_id"])
    op.create_foreign_key(
        "fk_devices_current_account_id",
        "devices",
        "endpoint_accounts",
        ["current_account_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "device_account_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("endpoint_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_type", sa.String(length=32), nullable=False),
        sa.Column("raw_value", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index("ix_device_account_history_device_id", "device_account_history", ["device_id"])
    op.create_index("ix_device_account_history_account_id", "device_account_history", ["account_id"])
    op.create_index("ix_device_account_history_seen_at", "device_account_history", ["seen_at"])
    op.create_index(
        "ix_device_account_history_account_time",
        "device_account_history",
        ["account_id", "seen_at"],
    )
    op.create_index(
        "ix_device_account_history_device_time",
        "device_account_history",
        ["device_id", "seen_at"],
    )


def downgrade():
    op.drop_table("device_account_history")
    op.drop_constraint("fk_devices_current_account_id", "devices", type_="foreignkey")
    op.drop_index("ix_devices_current_account_id", table_name="devices")
    op.drop_column("devices", "current_account_seen_at")
    op.drop_column("devices", "current_account_id")
    op.drop_table("action_kinds")
    op.drop_table("endpoint_accounts")
