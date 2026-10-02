"""Authz v2 role flags, script.is_published, admin_audit_log.

Revision ID: 0009_authz_audit
Revises: 0008_network_poll_runs
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_authz_audit"
down_revision = "0008_network_poll_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "is_viewer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_operator",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_password_viewer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "scripts",
        sa.Column(
            "is_published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_admin_audit_log_created_at",
        "admin_audit_log",
        ["created_at"],
    )
    op.create_index(
        "ix_admin_audit_log_actor_user_id",
        "admin_audit_log",
        ["actor_user_id"],
    )


def downgrade():
    op.drop_index("ix_admin_audit_log_actor_user_id", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_column("scripts", "is_published")
    op.drop_column("users", "is_password_viewer")
    op.drop_column("users", "is_operator")
    op.drop_column("users", "is_viewer")
