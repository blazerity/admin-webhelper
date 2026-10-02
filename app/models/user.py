"""Пользователи, вошедшие через LDAP.

Пароль не хранится. Колонку нельзя назвать is_active —
так называется свойство Flask-Login.
"""

from flask_login import UserMixin

from app.extensions import db
from app.utils import utcnow


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # sAMAccountName / uid; сравнение прав — без учёта регистра.
    username = db.Column(db.String(128), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(255), nullable=False, default="")
    email = db.Column(db.String(255), nullable=True)
    ldap_dn = db.Column(db.String(512), nullable=True)
    # Обновляется при каждом входе по членству в LDAP_ADMIN_GROUP.
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    ldap_groups = db.relationship(
        "UserLdapGroup",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User {self.username} admin={self.is_admin}>"


class UserLdapGroup(db.Model):
    """Группы LDAP на момент последнего входа (для прав на сектор)."""

    __tablename__ = "user_ldap_groups"
    __table_args__ = (
        db.UniqueConstraint("user_id", "group_name", name="uq_user_ldap_group"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # CN без DN; сравнение без регистра — в authz.
    group_name = db.Column(db.String(255), nullable=False, index=True)

    user = db.relationship("User", back_populates="ldap_groups")
