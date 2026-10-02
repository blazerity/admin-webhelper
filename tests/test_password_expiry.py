"""Тесты модуля уведомлений о сроке паролей AD."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.extensions import db
from app.models import PasswordNotification
from app.services.password_ad_client import AdUser, classify_user, compute_days_left
from app.services.password_expiry_service import run_password_expiry
from app.services.password_expiry_settings import (
    get_password_expiry_settings,
    set_password_expiry_settings,
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
