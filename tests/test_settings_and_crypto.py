from app.extensions import db
from app.models import RemoteCredential
from app.services.credential_service import (
    get_remote_admin_credentials,
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
        creds = get_remote_admin_credentials()
        assert creds.username == "winadmin"
        assert creds.password == "Sup3rSecret"
        assert "Sup3rSecret" not in repr(creds)


def test_blank_password_keeps_previous(app, admin_id):
    with app.app_context():
        save_remote_admin_credentials("winadmin", "CORP", "Sup3rSecret", admin_id)
        save_remote_admin_credentials("winadmin", "CORP", "", admin_id)
        assert get_remote_admin_credentials().password == "Sup3rSecret"


def test_poll_interval_is_clamped_and_saved(app):
    with app.app_context():
        assert get_poll_interval_seconds() == 300
        assert set_poll_interval_seconds(120) == 120
        assert get_poll_interval_seconds() == 120
        db.session.rollback()
