"""Начальная схема: пользователи, секторы, устройства, скрипты.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-02

device_history пока одна таблица. Когда объём вырастет, отдельной
миграцией её переводят на PARTITION BY RANGE (timestamp) по месяцам.
До этого момента достаточно индекса (device_id, timestamp).
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("ldap_dn", sa.String(length=512), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "user_ldap_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_name", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("user_id", "group_name", name="uq_user_ldap_group"),
    )
    op.create_index("ix_user_ldap_groups_group_name", "user_ldap_groups", ["group_name"])

    op.create_table(
        "sectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_sectors_name"),
    )

    op.create_table(
        "sector_ranges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sector_id", sa.Integer(), sa.ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cidr", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.String(length=255), nullable=False),
    )
    op.create_index("ix_sector_ranges_sector_id", "sector_ranges", ["sector_id"])

    op.create_table(
        "sector_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sector_id", sa.Integer(), sa.ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_name", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("sector_id", "subject_type", "subject_name", name="uq_sector_access_subject"),
    )
    op.create_index("ix_sector_access_sector_id", "sector_access", ["sector_id"])

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("mac", sa.String(length=17), nullable=True),
        sa.Column("sector_id", sa.Integer(), sa.ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=False),
        sa.Column("last_response_time_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip", name="uq_devices_ip"),
    )
    op.create_index("ix_devices_ip", "devices", ["ip"])
    op.create_index("ix_devices_hostname", "devices", ["hostname"])
    op.create_index("ix_devices_mac", "devices", ["mac"])
    op.create_index("ix_devices_sector_id", "devices", ["sector_id"])
    op.create_index("ix_devices_last_status", "devices", ["last_status"])

    op.create_table(
        "device_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_device_history_timestamp", "device_history", ["timestamp"])
    op.create_index("ix_device_history_device_time", "device_history", ["device_id", "timestamp"])

    op.create_table(
        "scripts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target_os", sa.String(length=32), nullable=False),
        sa.Column("interpreter", sa.String(length=32), nullable=False),
        sa.Column("storage", sa.String(length=16), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_scripts_name"),
    )

    op.create_table(
        "script_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("script_id", sa.Integer(), sa.ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("run_type", sa.String(length=16), nullable=False),
        sa.Column("command_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("log_text", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_script_runs_script_id", "script_runs", ["script_id"])
    op.create_index("ix_script_runs_device_id", "script_runs", ["device_id"])
    op.create_index("ix_script_runs_user_id", "script_runs", ["user_id"])
    op.create_index("ix_script_runs_batch_id", "script_runs", ["batch_id"])
    op.create_index("ix_script_runs_status", "script_runs", ["status"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "remote_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("domain", sa.String(length=128), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("name", name="uq_remote_credentials_name"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "COMMENT ON TABLE device_history IS "
            "'История опросов. Позже PARTITION BY RANGE (timestamp) по месяцам.'"
        )


def downgrade():
    op.drop_table("remote_credentials")
    op.drop_table("app_settings")
    op.drop_table("script_runs")
    op.drop_table("scripts")
    op.drop_table("device_history")
    op.drop_table("devices")
    op.drop_table("sector_access")
    op.drop_table("sector_ranges")
    op.drop_table("sectors")
    op.drop_table("user_ldap_groups")
    op.drop_table("users")
