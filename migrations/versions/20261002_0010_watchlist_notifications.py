"""device_watchlist and in-app notifications.

Revision ID: 0010_watchlist_notifications
Revises: 0009_authz_audit
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_watchlist_notifications"
down_revision = "0009_authz_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_watchlist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column(
            "offline_minutes",
            sa.Integer(),
            nullable=False,
            server_default="15",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "device_id", name="uq_device_watchlist_user_device"),
    )
    op.create_index(
        "ix_device_watchlist_user_id",
        "device_watchlist",
        ["user_id"],
    )
    op.create_index(
        "ix_device_watchlist_device_id",
        "device_watchlist",
        ["device_id"],
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index(
        "ix_notifications_user_read",
        "notifications",
        ["user_id", "read_at"],
    )


def downgrade():
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_device_watchlist_device_id", table_name="device_watchlist")
    op.drop_index("ix_device_watchlist_user_id", table_name="device_watchlist")
    op.drop_table("device_watchlist")
