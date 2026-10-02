"""Таблицы модуля уведомлений о сроке паролей AD.

Revision ID: 0006_password_expiry
Revises: 0005_accounts_actions
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_password_expiry"
down_revision = "0005_accounts_actions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "password_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("notification_date", sa.Date(), nullable=False),
        sa.Column("days_left_at_send", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pwd_last_set_snapshot", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_password_notifications_username",
        "password_notifications",
        ["username"],
    )
    op.create_index(
        "ix_password_notifications_notification_date",
        "password_notifications",
        ["notification_date"],
    )

    op.create_table(
        "password_expiry_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=False),
        sa.Column("send_emails", sa.Boolean(), nullable=False),
        sa.Column("user_mail_paused", sa.Boolean(), nullable=False),
        sa.Column("users_count", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("resolved_count", sa.Integer(), nullable=False),
        sa.Column("upcoming_count", sa.Integer(), nullable=False),
        sa.Column("overdue_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("password_expiry_runs")
    op.drop_index(
        "ix_password_notifications_notification_date",
        table_name="password_notifications",
    )
    op.drop_index(
        "ix_password_notifications_username",
        table_name="password_notifications",
    )
    op.drop_table("password_notifications")
