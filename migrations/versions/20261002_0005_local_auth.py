"""Локальный вход: колонка password_hash у users.

Revision ID: 0005_local_auth
Revises: 0004_device_serial
Create Date: 2026-10-02

Для LOCAL_AUTH_ENABLED у пользователя хранится werkzeug-хеш.
LDAP-учётки оставляют колонку пустой.
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_local_auth"
down_revision = "0004_device_serial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )


def downgrade():
    op.drop_column("users", "password_hash")
