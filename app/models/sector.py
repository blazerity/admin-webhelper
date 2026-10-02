"""Сектор — логическая группа устройств (проект, площадка, VLAN).

Диапазоны и права хранятся отдельными строками, чтобы их можно было
добавлять и удалять через веб-интерфейс без правки файлов.
"""

from app.extensions import db
from app.models.base import TimestampMixin


class Sector(TimestampMixin, db.Model):
    __tablename__ = "sectors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False, default="")

    ranges = db.relationship(
        "SectorRange",
        back_populates="sector",
        cascade="all, delete-orphan",
        order_by="SectorRange.id",
    )
    access_rules = db.relationship(
        "SectorAccess",
        back_populates="sector",
        cascade="all, delete-orphan",
    )
    devices = db.relationship(
        "Device",
        back_populates="sector",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Sector {self.name}>"


class SectorRange(db.Model):
    """Один IP или CIDR, как его ввёл администратор.

    Текст хранится как есть (после нормализации через ipaddress),
    чтобы на экране редактирования показать исходный диапазон,
    а не тысячи развёрнутых адресов.
    """

    __tablename__ = "sector_ranges"

    id = db.Column(db.Integer, primary_key=True)
    sector_id = db.Column(
        db.Integer,
        db.ForeignKey("sectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cidr = db.Column(db.String(64), nullable=False)
    comment = db.Column(db.String(255), nullable=False, default="")

    sector = db.relationship("Sector", back_populates="ranges")


class SectorAccess(db.Model):
    """Кому виден сектор: конкретному пользователю или LDAP-группе.

    subject_type: 'user' или 'group'.
    subject_name: username или CN группы.
    Администратор приложения видит все секторы и без строки в этой таблице.
    """

    __tablename__ = "sector_access"
    __table_args__ = (
        db.UniqueConstraint(
            "sector_id",
            "subject_type",
            "subject_name",
            name="uq_sector_access_subject",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sector_id = db.Column(
        db.Integer,
        db.ForeignKey("sectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_type = db.Column(db.String(16), nullable=False)
    subject_name = db.Column(db.String(255), nullable=False)

    sector = db.relationship("Sector", back_populates="access_rules")
