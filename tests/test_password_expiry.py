"""Тесты модуля уведомлений о сроке паролей AD."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.models import PasswordNotification
from app.services.password_ad_client import AdUser, classify_user, compute_days_left
from app.services.password_expiry_service import run_password_expiry
from app.services.password_expiry_settings import (
    get_ldap_bind_settings,
    get_password_expiry_settings,
    get_smtp_settings,
    set_ldap_bind_settings,
    set_password_expiry_settings,
    set_smtp_settings,
)
from app.services.password_notification_tracker import NotificationTracker


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _user(
    *,
    username: str = "jdoe",
    days_left: int | None = 4,
    status: str = "upcoming",
    snapshot: str = "100",
) -> AdUser:
    today = date.today()
    expiry = None if days_left is None else today + timedelta(days=days_left)
    return AdUser(
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        distinguished_name=f"CN={username},DC=example,DC=com",
        user_account_control=512,
        pwd_last_set=datetime.now(timezone.utc) - timedelta(days=176),
        pwd_last_set_snapshot=snapshot,
        days_left=days_left,
        expiry_date=expiry,
        status=status,  # type: ignore[arg-type]
    )


def test_compute_and_classify():
    pwd = datetime(2026, 1, 1, tzinfo=timezone.utc)
    days, expiry = compute_days_left(pwd, 180, today=date(2026, 6, 25))
    assert days == 5
    assert expiry == date(2026, 6, 30)
    assert classify_user(5, 5) == "upcoming"
    assert classify_user(0, 5) == "overdue"
    assert classify_user(None, 5, must_change=True) == "must_change"


def test_settings_reuse_ldap_base_dn(app):
    with app.app_context():
        settings = get_password_expiry_settings()
        assert settings.search_base == "DC=example,DC=com"
        assert settings.search_base_override == ""
        set_password_expiry_settings(
            max_pwd_age_days=90,
            first_warning_days=7,
            daily_warning_threshold=3,
            search_base="OU=Users,DC=example,DC=com",
            admin_recipients="ops@example.com",
        )
        settings = get_password_expiry_settings()
        assert settings.max_pwd_age_days == 90
        assert settings.search_base == "OU=Users,DC=example,DC=com"
        assert settings.admin_recipients == ["ops@example.com"]


def test_ldap_bind_and_smtp_ui_override(app):
    with app.app_context():
        app.config["LDAP_BIND_DN"] = "CN=env,DC=example,DC=com"
        app.config["LDAP_BIND_PASSWORD"] = "env-secret"
        app.config["SMTP_HOST"] = "env-mail.example.com"
        app.config["SMTP_FROM"] = "env@example.com"
        app.config["SMTP_PASSWORD"] = "env-smtp"

        ldap = get_ldap_bind_settings()
        assert ldap.bind_dn == "CN=env,DC=example,DC=com"
        assert ldap.bind_password == "env-secret"
        assert ldap.source == "env"

        set_ldap_bind_settings(
            bind_dn="CN=ui,DC=example,DC=com",
            bind_password="ui-secret",
        )
        ldap = get_ldap_bind_settings()
        assert ldap.bind_dn == "CN=ui,DC=example,DC=com"
        assert ldap.bind_password == "ui-secret"
        assert ldap.source == "ui"

        # Пустой пароль сохраняет прежний шифротекст
        set_ldap_bind_settings(bind_dn="CN=ui2,DC=example,DC=com", bind_password="")
        ldap = get_ldap_bind_settings()
        assert ldap.bind_dn == "CN=ui2,DC=example,DC=com"
        assert ldap.bind_password == "ui-secret"

        set_smtp_settings(
            host="smtp.ui.example.com",
            port=587,
            use_starttls=True,
            from_address="noreply@ui.example.com",
            username="mailer",
            password="smtp-secret",
        )
        smtp = get_smtp_settings()
        assert smtp.host == "smtp.ui.example.com"
        assert smtp.port == 587
        assert smtp.use_starttls is True
        assert smtp.from_address == "noreply@ui.example.com"
        assert smtp.username == "mailer"
        assert smtp.password == "smtp-secret"
        assert smtp.configured is True
        assert smtp.source == "ui"

        set_smtp_settings(password="")
        assert get_smtp_settings().password == "smtp-secret"


def test_tracker_first_warning_and_dedupe(app):
    with app.app_context():
        tracker = NotificationTracker()
        user = _user(days_left=5, status="upcoming")
        decision = tracker.decide(
            user,
            first_warning_days=5,
            daily_warning_threshold=3,
            today=date.today(),
        )
        assert decision.action == "send"
        tracker.append(user, status="upcoming", today=date.today())
        tracker.commit()

        tracker2 = NotificationTracker()
        again = tracker2.decide(
            user,
            first_warning_days=5,
            daily_warning_threshold=3,
            today=date.today(),
        )
        assert again.action == "skip"
        assert PasswordNotification.query.count() == 1


def test_dashboard_forbidden_for_user(client, alice_id):
    _login(client, alice_id)
    response = client.get("/password-expiry/")
    assert response.status_code == 403


def test_dashboard_ok_for_admin(client, admin_id):
    _login(client, admin_id)
    response = client.get("/password-expiry/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Пароли AD" in html
    assert 'href="/password-expiry/"' in html or "password_expiry.dashboard" in html
    # Отчёт — не внутри layout настроек
    assert "settings-sidebar" not in html


def test_settings_page_in_settings_layout(client, admin_id):
    _login(client, admin_id)
    response = client.get("/password-expiry/settings")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "settings-sidebar" in html
    assert "ldap_bind_dn" in html
    assert "smtp_host" in html


def test_settings_save_ldap_and_smtp(client, admin_id, app):
    _login(client, admin_id)
    response = client.post(
        "/password-expiry/settings",
        data={
            "form": "ldap_bind",
            "ldap_bind_dn": "CN=svc,DC=example,DC=com",
            "ldap_bind_password": "BindSecret",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Учётка LDAP для модуля сохранена." in response.get_data(as_text=True)

    response = client.post(
        "/password-expiry/settings",
        data={
            "form": "smtp",
            "smtp_host": "mail.example.com",
            "smtp_port": "587",
            "smtp_from": "noreply@example.com",
            "smtp_user": "mailer",
            "smtp_password": "SmtpSecret",
            "smtp_use_starttls": "1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Настройки SMTP сохранены." in response.get_data(as_text=True)

    with app.app_context():
        ldap = get_ldap_bind_settings()
        assert ldap.bind_dn == "CN=svc,DC=example,DC=com"
        assert ldap.bind_password == "BindSecret"
        smtp = get_smtp_settings()
        assert smtp.host == "mail.example.com"
        assert smtp.password == "SmtpSecret"


def test_top_nav_has_password_expiry_for_admin(client, admin_id):
    _login(client, admin_id)
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert 'href="/password-expiry/"' in html
    assert "Пароли AD" in html


def test_run_pipeline_dry_run_mocked(app, admin_id):
    users = [
        _user(username="soon", days_left=4, status="upcoming"),
        _user(username="late", days_left=-2, status="overdue", snapshot="200"),
    ]
    with app.app_context():
        set_password_expiry_settings(admin_recipients="admin@example.com")
        with patch(
            "app.services.password_expiry_service.fetch_password_users",
            return_value=users,
        ):
            result = run_password_expiry(send_emails=False, mode="dry-run")
        assert result.exit_code == 0
        assert result.users_count == 2
        assert result.upcoming_count == 1
        assert result.overdue_count == 1
        # dry-run не пишет историю
        assert PasswordNotification.query.count() == 0
