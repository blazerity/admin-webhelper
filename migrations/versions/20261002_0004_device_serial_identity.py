"""Серийный номер устройства и снятие уникальности IP.

Revision ID: 0004_device_serial
Revises: 0003_psexec_per_user
Create Date: 2026-10-02

Уникальность машины — serial_number (service tag), а не IP.
IP остаётся текущим адресом и может меняться при переезде.
Пустые заготовки (ни разу не отвечали) удаляются: на карте
незачем показывать адреса, за которыми никто не сидит.
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_device_serial"
down_revision = "0003_psexec_per_user"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "devices",
        sa.Column("serial_number", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_devices_serial_number", "devices", ["serial_number"])
    op.create_unique_constraint("uq_devices_serial_number", "devices", ["serial_number"])
    op.drop_constraint("uq_devices_ip", "devices", type_="unique")
    # Заготовки на весь CIDR: статус unknown и ни одного успешного ответа.
    op.execute(
        "DELETE FROM devices WHERE last_seen IS NULL AND last_status = 'unknown'"
    )


def downgrade():
    # Перед возвратом уникальности IP оставляем одну строку на адрес
    # (с наибольшим id), иначе PostgreSQL/SQLite отвергнут constraint.
    op.execute(
        """
        DELETE FROM devices
        WHERE id NOT IN (
            SELECT MAX(id) FROM devices GROUP BY ip
        )
        """
    )
    op.create_unique_constraint("uq_devices_ip", "devices", ["ip"])
    op.drop_constraint("uq_devices_serial_number", "devices", type_="unique")
    op.drop_index("ix_devices_serial_number", table_name="devices")
    op.drop_column("devices", "serial_number")
