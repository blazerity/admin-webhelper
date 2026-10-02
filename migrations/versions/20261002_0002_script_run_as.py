"""От чьего имени выполняется скрипт: учётка PsExec или SYSTEM.

Revision ID: 0002_script_run_as
Revises: 0001_initial
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_script_run_as"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "scripts",
        sa.Column("run_as", sa.String(length=16), nullable=False, server_default="psexec"),
    )
    op.add_column(
        "script_runs",
        sa.Column("run_as", sa.String(length=16), nullable=False, server_default="psexec"),
    )


def downgrade():
    op.drop_column("script_runs", "run_as")
    op.drop_column("scripts", "run_as")
