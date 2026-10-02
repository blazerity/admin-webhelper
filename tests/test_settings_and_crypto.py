from app.extensions import db
from app.models import RemoteCredential
from app.services.credential_service import (
    get_remote_admin_credentials,
    remember_login_password,
    save_remote_admin_credentials,
)
from app.services.crypto_service import decrypt, encrypt
from app.services.settings_service import get_poll_interval_seconds, set_poll_interval_seconds


def test_encrypt_roundtrip(app):
    with app.app_context():
        token = encrypt("secret-password")
        assert token != "secret-password"
        assert decrypt(token) == "secret-password"


def test_password_is_stored_encrypted(app, admin_id):
    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        row = RemoteCredential.query.one()
        assert "Sup3rSecret" not in row.password_encrypted
        creds = get_remote_admin_credentials(admin_id)
        assert creds.username == "winadmin"
        assert creds.password == "Sup3rSecret"
        assert "Sup3rSecret" not in repr(creds)


def test_blank_password_keeps_previous(app, admin_id):
    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        save_remote_admin_credentials("winadmin", "CORP", "", admin_id)
        assert get_remote_admin_credentials(admin_id).password == "Sup3rSecret"


def test_psexec_credentials_are_per_user(app, admin_id, alice_id):
    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "AdminSecret", admin_id)
        save_remote_admin_credentials("alicewin", "LAB", "AliceSecret", alice_id)
        admin = get_remote_admin_credentials(admin_id)
        alice = get_remote_admin_credentials(alice_id)
        assert admin.username == "winadmin"
        assert admin.password == "AdminSecret"
        assert alice.username == "alicewin"
        assert alice.domain == "LAB"
        assert alice.password == "AliceSecret"
        assert RemoteCredential.query.count() == 2


def test_empty_psexec_uses_site_login(app, admin_id):
    with app.app_context():
        app.config["LDAP_DOMAIN"] = "CORP"
        remember_login_password(admin_id, "SiteSecret")
        creds = get_remote_admin_credentials(admin_id)
        assert creds.username == "admin"
        assert creds.domain == "CORP"
        assert creds.password == "SiteSecret"
        row = RemoteCredential.query.filter_by(user_id=admin_id).one()
        assert row.username == ""
        assert "SiteSecret" not in row.login_password_encrypted


def test_clearing_psexec_keeps_login_and_drops_custom_account(app, admin_id):
    with app.app_context():
        app.config["LDAP_DOMAIN"] = "CORP"
        remember_login_password(admin_id, "SiteSecret")
        save_remote_admin_credentials("winadmin", "OTHER", "Sup3rSecret", admin_id)
        assert get_remote_admin_credentials(admin_id).username == "winadmin"
        save_remote_admin_credentials("", "", "", admin_id)
        creds = get_remote_admin_credentials(admin_id)
        assert creds.username == "admin"
        assert creds.domain == "CORP"
        assert creds.password == "SiteSecret"


def test_poll_interval_is_clamped_and_saved(app):
    with app.app_context():
        assert get_poll_interval_seconds() == 300
        assert set_poll_interval_seconds(120) == 120
        assert get_poll_interval_seconds() == 120
        db.session.rollback()
