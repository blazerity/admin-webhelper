from app.extensions import db
from app.models import AppSetting, RemoteCredential
from app.services.credential_service import (
    get_remote_admin_credentials,
    remember_login_password,
    save_remote_admin_credentials,
)
from app.services.crypto_service import decrypt, encrypt
from app.services.discovery_service import discovery_credentials
from app.services.settings_service import (
    DISCOVERY_PASSWORD_KEY,
    DiscoveryCredentialsError,
    UpdateSudoUserError,
    get_discovery_credential_view,
    get_poll_interval_seconds,
    get_stored_discovery_credentials,
    get_update_sudo_credentials,
    get_update_sudo_user,
    get_update_sudo_view,
    save_discovery_credentials,
    save_update_sudo_credentials,
    set_poll_interval_seconds,
    set_update_sudo_user,
)


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


def test_discovery_credentials_are_stored_encrypted(app):
    with app.app_context():
        save_discovery_credentials("wmisvc", "CORP", "WmiSecret!")
        view = get_discovery_credential_view()
        assert view.username == "wmisvc"
        assert view.domain == "CORP"
        assert view.password_set is True

        row = db.session.get(AppSetting, DISCOVERY_PASSWORD_KEY)
        assert row is not None
        assert "WmiSecret!" not in row.value

        stored = get_stored_discovery_credentials()
        assert stored is not None
        assert stored.password == "WmiSecret!"
        assert "WmiSecret!" not in repr(stored)


def test_discovery_blank_password_keeps_previous(app):
    with app.app_context():
        save_discovery_credentials("wmisvc", "CORP", "WmiSecret!")
        save_discovery_credentials("wmisvc", "LAB", "")
        stored = get_stored_discovery_credentials()
        assert stored is not None
        assert stored.domain == "LAB"
        assert stored.password == "WmiSecret!"


def test_discovery_clear_falls_back_to_env(app):
    with app.app_context():
        app.config["DISCOVERY_USERNAME"] = "envuser"
        app.config["DISCOVERY_PASSWORD"] = "envpass"
        app.config["DISCOVERY_DOMAIN"] = "ENV"
        save_discovery_credentials("dbuser", "DB", "DbSecret")
        creds = discovery_credentials()
        assert creds is not None
        assert creds.username == "dbuser"
        assert creds.password == "DbSecret"

        save_discovery_credentials("", "", "")
        creds = discovery_credentials()
        assert creds is not None
        assert creds.username == "envuser"
        assert creds.password == "envpass"
        assert creds.domain == "ENV"


def test_discovery_requires_password_on_first_save(app):
    with app.app_context():
        try:
            save_discovery_credentials("wmisvc", "CORP", "")
            assert False, "ожидали DiscoveryCredentialsError"
        except DiscoveryCredentialsError:
            pass


def test_update_sudo_user_saved_and_falls_back_to_env(app):
    with app.app_context():
        app.config["UPDATE_SUDO_USER"] = "fromenv"
        assert get_update_sudo_user() == "fromenv"
        assert set_update_sudo_user("root") == "root"
        assert get_update_sudo_user() == "root"
        assert set_update_sudo_user("") == ""
        assert get_update_sudo_user() == "fromenv"


def test_update_sudo_user_rejects_bad_names(app):
    with app.app_context():
        for bad in ("Root", "has space", "../etc", "a" * 40, "user;rm"):
            try:
                set_update_sudo_user(bad)
                assert False, f"ожидали ошибку для {bad!r}"
            except UpdateSudoUserError:
                pass


def test_update_sudo_password_is_stored_encrypted(app):
    with app.app_context():
        view = save_update_sudo_credentials("root", "RootSecret!")
        assert view.username == "root"
        assert view.password_set is True
        assert "RootSecret!" not in repr(get_update_sudo_view())

        creds = get_update_sudo_credentials()
        assert creds.username == "root"
        assert creds.password == "RootSecret!"
        assert "RootSecret!" not in repr(creds)

        save_update_sudo_credentials("root", "")
        assert get_update_sudo_credentials().password == "RootSecret!"

        save_update_sudo_credentials("", "")
        cleared = get_update_sudo_view()
        assert cleared.username == ""
        assert cleared.password_set is False


def test_update_sudo_password_without_user_rejected(app):
    with app.app_context():
        try:
            save_update_sudo_credentials("", "secret")
            assert False, "ожидали UpdateSudoUserError"
        except UpdateSudoUserError:
            pass
