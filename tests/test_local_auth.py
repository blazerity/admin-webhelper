"""Локальный вход без LDAP."""

from sqlalchemy import select

from app.extensions import db
from app.models import User
from app.services.local_auth_service import authenticate_local, ensure_local_admin


def test_login_page_shows_auth_method_choice(client):
    response = client.get("/login")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Способ входа" in html
    assert 'name="auth_method" value="ldap"' in html
    assert 'name="auth_method" value="local"' in html
    assert 'value="ldap"' in html and "checked" in html


def test_local_admin_is_seeded_on_login_page(client, app):
    with app.app_context():
        assert db.session.scalar(select(User).where(User.username == "testadmin")) is None

    response = client.get("/login")
    assert response.status_code == 200

    with app.app_context():
        user = db.session.scalar(select(User).where(User.username == "testadmin"))
        assert user is not None
        assert user.is_admin is True
        assert user.password_hash
        assert user.display_name == "Локальный администратор"


def test_local_login_success(client, app):
    client.get("/login")
    response = client.post(
        "/login?next=/health",
        data={
            "username": "testadmin",
            "password": "testadmin",
            "auth_method": "local",
        },
    )
    assert response.status_code == 302
    assert "/health" in response.headers["Location"]

    again = client.get("/login")
    assert again.status_code == 302
    assert again.headers["Location"].endswith("/")


def test_local_login_wrong_password(client, app):
    client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": "wrong",
            "auth_method": "local",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Неверное имя или пароль." in html
    assert 'value="local"' in html and "checked" in html


def test_local_login_disabled(client, app):
    app.config["LOCAL_AUTH_ENABLED"] = False
    response = client.get("/login")
    html = response.get_data(as_text=True)
    assert "Способ входа" not in html

    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": "testadmin",
            "auth_method": "local",
        },
    )
    # Без LOCAL_AUTH_ENABLED «local» игнорируется и уходит в LDAP.
    assert response.status_code == 200
    assert "Неверное имя или пароль." in response.get_data(as_text=True)


def test_ensure_local_admin_updates_password(app):
    with app.app_context():
        first = ensure_local_admin()
        assert first is not None
        old_hash = first.password_hash

        app.config["LOCAL_ADMIN_PASSWORD"] = "new-secret"
        second = ensure_local_admin()
        assert second is not None
        assert second.id == first.id
        assert second.password_hash != old_hash
        assert authenticate_local("testadmin", "new-secret") is not None
        assert authenticate_local("testadmin", "testadmin") is None
