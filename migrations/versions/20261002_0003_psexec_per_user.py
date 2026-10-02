"""Учётка PsExec отдельно для каждого пользователя сайта.

Revision ID: 0003_psexec_per_user
Revises: 0002_script_run_as
Create Date: 2026-10-02

Раньше в remote_credentials была одна строка name=default на всех.
Теперь строка принадлежит user_id. Старую строку оставляем тому,
кто её последним сохранил (updated_by_id). Строку без автора удаляем:
её нельзя честно отдать одному пользователю.
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_psexec_per_user"
down_revision = "0002_script_run_as"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("remote_credentials", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column(
        "remote_credentials",
        sa.Column("login_password_encrypted", sa.Text(), nullable=False, server_default=""),
    )
    op.execute(
        "UPDATE remote_credentials SET user_id = updated_by_id "
        "WHERE user_id IS NULL AND updated_by_id IS NOT NULL"
    )
    op.execute("DELETE FROM remote_credentials WHERE user_id IS NULL")
    op.alter_column("remote_credentials", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_remote_credentials_user_id",
        "remote_credentials",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_remote_credentials_user_id",
        "remote_credentials",
        ["user_id"],
    )
    op.drop_constraint("uq_remote_credentials_name", "remote_credentials", type_="unique")
    op.drop_column("remote_credentials", "name")


def downgrade():
    op.add_column("remote_credentials", sa.Column("name", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE remote_credentials SET name = 'default' "
        "WHERE id = (SELECT MIN(id) FROM remote_credentials)"
    )
    op.execute("DELETE FROM remote_credentials WHERE name IS NULL")
    op.alter_column("remote_credentials", "name", nullable=False)
    op.create_unique_constraint(
        "uq_remote_credentials_name",
        "remote_credentials",
        ["name"],
    )
    op.drop_constraint("uq_remote_credentials_user_id", "remote_credentials", type_="unique")
    op.drop_constraint("fk_remote_credentials_user_id", "remote_credentials", type_="foreignkey")
    op.drop_column("remote_credentials", "user_id")
    op.drop_column("remote_credentials", "login_password_encrypted")
