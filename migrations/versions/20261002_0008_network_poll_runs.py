"""Журнал прогонов опроса сети.

Revision ID: 0008_network_poll_runs
Revises: 0007_login_services
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_network_poll_runs"
down_revision = "0007_login_services"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "network_poll_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scanned", sa.Integer(), nullable=False),
        sa.Column("online", sa.Integer(), nullable=False),
        sa.Column("offline", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
    )
    op.create_index(
        "ix_network_poll_runs_started_at",
        "network_poll_runs",
        ["started_at"],
    )


def downgrade():
    op.drop_index("ix_network_poll_runs_started_at", table_name="network_poll_runs")
    op.drop_table("network_poll_runs")
