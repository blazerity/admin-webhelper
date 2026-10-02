"""Общие фикстуры.

Тесты ходят в SQLite в памяти, LDAP и сеть не нужны.
FERNET_KEY задаётся до создания приложения, иначе шифрование в тестах упадёт.
"""

import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.extensions import db
from app.models import User, UserLdapGroup


@pytest.fixture
def app():
    application = create_app("testing")
    application.config["FERNET_KEY"] = os.environ["FERNET_KEY"]
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_id(app):
    with app.app_context():
        user = User(username="admin", display_name="Администратор", is_admin=True)
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def alice_id(app):
    """Обычный пользователь без групп."""
    with app.app_context():
        user = User(username="alice", display_name="Алиса", is_admin=False)
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def carol_id(app):
    """Пользователь в группе netops."""
    with app.app_context():
        user = User(username="carol", display_name="Каролина", is_admin=False)
        user.ldap_groups.append(UserLdapGroup(group_name="netops"))
        db.session.add(user)
        db.session.commit()
        return user.id
