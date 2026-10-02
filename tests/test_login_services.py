"""Сервисы на экране входа: CRUD, валидация адреса, публичный статус."""

from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import DeviceStatus, LoginService
from app.services.login_service_status import LoginServiceError, save_service
from app.services.net_utils import NetworkInputError, assert_host_or_ipv4
from app.services.ping_service import PingResult


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_assert_host_or_ipv4_accepts_ip_and_fqdn():
    assert assert_host_or_ipv4("10.1.2.3") == "10.1.2.3"
    assert assert_host_or_ipv4("DC01.Corp.Local.") == "dc01.corp.local"
    assert assert_host_or_ipv4("mail") == "mail"


def test_assert_host_or_ipv4_rejects_junk():
    with pytest.raises(NetworkInputError):
        assert_host_or_ipv4("bad host")
    with pytest.raises(NetworkInputError):
        assert_host_or_ipv4("host;rm")
    with pytest.raises(NetworkInputError):
        assert_host_or_ipv4("")


def test_save_service_and_list_page(client, app, admin_id):
    _login(client, admin_id)
    response = client.post(
        "/login-services/",
        data={
            "name": "Контроллер домена",
            "address": "10.0.0.10",
            "sort_order": "1",
            "is_enabled": "on",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Контроллер домена" in html
    assert "10.0.0.10" in html

    with app.app_context():
        service = db.session.query(LoginService).one()
        assert service.name == "Контроллер домена"
        assert service.address == "10.0.0.10"
        assert service.is_enabled is True


def test_login_services_admin_only(client, alice_id):
    _login(client, alice_id)
    response = client.get("/login-services/")
    assert response.status_code == 403


def test_duplicate_name_rejected(app):
    with app.app_context():
        save_service(None, "VPN", "10.0.0.1")
        with pytest.raises(LoginServiceError):
            save_service(None, "VPN", "10.0.0.2")


def test_login_page_shows_status_panel(client, app):
    with app.app_context():
        db.session.add(
            LoginService(
                name="Почта",
                address="mail.corp.local",
                sort_order=0,
                is_enabled=True,
            )
        )
        db.session.commit()

    response = client.get("/login")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Доступность сервисов" in html
    assert "Почта" in html
    assert "mail.corp.local" in html
    assert "login_status.js" in html


def test_login_page_hides_panel_when_empty(client):
    response = client.get("/login")
    html = response.get_data(as_text=True)
    assert "Доступность сервисов" not in html
    assert "login-layout" not in html


def test_public_status_api(client, app):
    with app.app_context():
        db.session.add(
            LoginService(
                name="DNS",
                address="10.0.0.53",
                sort_order=0,
                is_enabled=True,
            )
        )
        db.session.add(
            LoginService(
                name="Скрытый",
                address="10.0.0.99",
                sort_order=1,
                is_enabled=False,
            )
        )
        db.session.commit()

    fake = PingResult(DeviceStatus.ONLINE, 12, "ok")
    with patch(
        "app.services.login_service_status.resolve_to_ipv4",
        return_value="10.0.0.53",
    ), patch(
        "app.services.login_service_status.ping_host",
        return_value=fake,
    ):
        response = client.get("/api/login-services/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["services"]) == 1
    assert payload["services"][0]["name"] == "DNS"
    assert payload["services"][0]["online"] is True
    assert payload["services"][0]["latency_ms"] == 12
