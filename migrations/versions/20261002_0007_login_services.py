"""Сервисы для блока доступности на экране входа.

Revision ID: 0007_login_services
Revises: 0006_password_expiry
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_login_services"
down_revision = "0006_password_expiry"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_login_services_name"),
    )
    op.create_index("ix_login_services_sort_order", "login_services", ["sort_order"])


def downgrade():
    op.drop_index("ix_login_services_sort_order", table_name="login_services")
    op.drop_table("login_services")
