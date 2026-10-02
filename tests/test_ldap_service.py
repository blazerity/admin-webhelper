"""LDAP без сети.

Соединение с каталогом подменяется: тесты не должны зависеть от того,
есть ли в окружении контроллер домена. Фикстуры app и client — из conftest.
"""

import logging
from urllib.parse import urlparse

from ldap3.core.exceptions import LDAPBindError
from sqlalchemy import select

from app.extensions import db
from app.models import User, UserLdapGroup
from app.services.ldap_service import LdapIdentity, authenticate, upsert_local_user


class _Entry:
    def __init__(self, dn: str, attributes: dict[str, list[str]]):
        self.entry_dn = dn
        self.entry_attributes_as_dict = attributes


class _Directory:
    """Запоминает, кем bind'ились и кто выполнял поиск."""

    def __init__(self, entry: _Entry):
        self.entry = entry
        self.servers: list[dict] = []
        self.binds: list[str] = []
        self.searches: list[dict] = []


def _configure_ldap(app, *, service_dn: str = "") -> None:
    app.config["LDAP_HOST"] = "ldap.invalid"
    app.config["LDAP_PORT"] = 636
    app.config["LDAP_USE_SSL"] = True
    app.config["LDAP_BASE_DN"] = "DC=example,DC=com"
    app.config["LDAP_BIND_DN"] = service_dn
    app.config["LDAP_BIND_PASSWORD"] = "svc-secret" if service_dn else ""
    app.config["LDAP_USER_FILTER"] = "(sAMAccountName={username})"
    app.config["LDAP_ADMIN_GROUP"] = "bawh-admins"
    app.config["LDAP_DOMAIN"] = "example.com"


def _install_directory(monkeypatch, directory: _Directory, *, bad_password: str | None = None) -> None:
    class FakeServer:
        def __init__(self, host, port=None, use_ssl=False, connect_timeout=None, **kwargs):
            directory.servers.append(
                {
                    "host": host,
                    "port": port,
                    "use_ssl": use_ssl,
                    "connect_timeout": connect_timeout,
                }
            )

    class FakeConnection:
        def __init__(self, server, user=None, password=None, auto_bind=False, **kwargs):
            self.user = user
            self.entries: list[_Entry] = []
            directory.binds.append(user)
            if bad_password is not None and password == bad_password:
                raise LDAPBindError("invalidCredentials")
            if auto_bind is not True:
                raise AssertionError("bind должен быть автоматическим, иначе ошибка пароля потеряется")

        def search(self, search_base, search_filter, search_scope, attributes=None):
            directory.searches.append(
                {
                    "user": self.user,
                    "base": search_base,
                    "filter": search_filter,
                    "scope": search_scope,
                    "attributes": list(attributes or []),
                }
            )
            self.entries = [directory.entry]
            return True

        def unbind(self):
            return None

    monkeypatch.setattr("app.services.ldap_service.Server", FakeServer)
    monkeypatch.setattr("app.services.ldap_service.Connection", FakeConnection)


def _admin_entry() -> _Entry:
    return _Entry(
        "CN=Alice,OU=Users,DC=example,DC=com",
        {
            "sAMAccountName": ["Alice"],
            "displayName": ["Алиса"],
            "mail": ["alice@example.com"],
            "cn": ["Alice"],
            "memberOf": [
                "CN=BAWH-Admins,OU=Groups,DC=example,DC=com",
                "CN=netops,OU=Groups,DC=example,DC=com",
            ],
        },
    )


def _path(response) -> str:
    return urlparse(response.headers["Location"]).path


def test_empty_password_returns_none(app, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("пустой пароль не должен открывать соединение с LDAP")

    monkeypatch.setattr("app.services.ldap_service.Server", explode)
    monkeypatch.setattr("app.services.ldap_service.Connection", explode)
    assert authenticate("alice", "") is None


def test_invalid_username_returns_none(app, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("имя с символами фильтра не должно уходить в LDAP")

    monkeypatch.setattr("app.services.ldap_service.Server", explode)
    monkeypatch.setattr("app.services.ldap_service.Connection", explode)
    assert authenticate("alice)(objectClass=*", "secret") is None
    assert authenticate("a" * 129, "secret") is None


def test_successful_bind_creates_local_admin(app, monkeypatch):
    _configure_ldap(app, service_dn="")
    directory = _Directory(_admin_entry())
    _install_directory(monkeypatch, directory)

    identity = authenticate("Alice", "correct")
    assert identity is not None
    assert identity.username == "alice"
    assert identity.display_name == "Алиса"
    assert identity.email == "alice@example.com"
    assert identity.dn == "CN=Alice,OU=Users,DC=example,DC=com"
    assert identity.groups == ["BAWH-Admins", "netops"]
    assert identity.is_admin is True
    assert directory.binds == ["Alice@example.com"]
    assert directory.servers[0]["connect_timeout"] == 5
    assert directory.searches[0]["user"] == "Alice@example.com"
    assert directory.searches[0]["base"] == "DC=example,DC=com"
    assert directory.searches[0]["filter"] == "(sAMAccountName=Alice)"
    assert directory.searches[0]["scope"] == "SUBTREE"
    assert set(directory.searches[0]["attributes"]) == {
        "displayName",
        "mail",
        "memberOf",
        "cn",
        "sAMAccountName",
    }

    user = upsert_local_user(identity)
    assert user.username == "alice"
    assert user.is_admin is True
    assert {row.group_name for row in user.ldap_groups} == {"BAWH-Admins", "netops"}
    assert user.last_login_at is not None


def test_service_account_searches_after_user_bind(app, monkeypatch):
    _configure_ldap(app, service_dn="CN=svc,DC=example,DC=com")
    directory = _Directory(_admin_entry())
    _install_directory(monkeypatch, directory)

    identity = authenticate("Alice", "correct")
    assert identity is not None
    assert identity.is_admin is True
    assert directory.binds == ["Alice@example.com", "CN=svc,DC=example,DC=com"]
    assert directory.searches[0]["user"] == "CN=svc,DC=example,DC=com"


def test_bad_bind_returns_none(app, monkeypatch, caplog):
    _configure_ldap(app, service_dn="")
    directory = _Directory(_admin_entry())
    _install_directory(monkeypatch, directory, bad_password="SecretPass")

    with caplog.at_level(logging.WARNING):
        assert authenticate("alice", "SecretPass") is None

    assert directory.searches == []
    assert "SecretPass" not in caplog.text


def test_ldap_url_does_not_keep_explicit_port(app, monkeypatch):
    """ldap:// как в AD Password Notifier не должен оставаться на порту 636."""
    _configure_ldap(app)
    app.config["LDAP_HOST"] = "ldap://dc01.example.com/"
    app.config["LDAP_PORT"] = 636
    app.config["LDAP_USE_SSL"] = True
    directory = _Directory(_admin_entry())
    _install_directory(monkeypatch, directory)

    assert authenticate("Alice", "correct") is not None
    assert directory.servers[0]["host"] == "ldap://dc01.example.com"
    assert directory.servers[0]["port"] is None
    assert directory.servers[0]["use_ssl"] is False


def test_ldaps_url_forces_ssl(app, monkeypatch):
    _configure_ldap(app)
    app.config["LDAP_HOST"] = "ldaps://dc01.example.com"
    app.config["LDAP_PORT"] = 389
    app.config["LDAP_USE_SSL"] = False
    directory = _Directory(_admin_entry())
    _install_directory(monkeypatch, directory)

    assert authenticate("Alice", "correct") is not None
    assert directory.servers[0]["host"] == "ldaps://dc01.example.com"
    assert directory.servers[0]["port"] is None
    assert directory.servers[0]["use_ssl"] is True


def test_port_636_without_ssl_uses_tls(app, monkeypatch, caplog):
    _configure_ldap(app)
    app.config["LDAP_HOST"] = "dc01.example.com"
    app.config["LDAP_PORT"] = 636
    app.config["LDAP_USE_SSL"] = False
    directory = _Directory(_admin_entry())
    _install_directory(monkeypatch, directory)

    with caplog.at_level(logging.WARNING):
        assert authenticate("Alice", "correct") is not None
    assert directory.servers[0]["port"] == 636
    assert directory.servers[0]["use_ssl"] is True
    assert "LDAP_PORT=389" in caplog.text


def test_empty_host_returns_none(app, monkeypatch, caplog):
    _configure_ldap(app)
    app.config["LDAP_HOST"] = "  "

    def explode(*args, **kwargs):
        raise AssertionError("без адреса каталога сеть не нужна")

    monkeypatch.setattr("app.services.ldap_service.Server", explode)
    with caplog.at_level(logging.ERROR):
        assert authenticate("alice", "secret") is None
    assert "LDAP_HOST" in caplog.text


def test_upsert_removes_old_group(app):
    with app.app_context():
        user = User(username="carol", display_name="Старое", is_admin=True)
        user.ldap_groups.append(UserLdapGroup(group_name="old-group"))
        user.ldap_groups.append(UserLdapGroup(group_name="netops"))
        db.session.add(user)
        db.session.commit()

        saved = upsert_local_user(
            LdapIdentity(
                username="carol",
                display_name="Каролина",
                email="carol@example.com",
                dn="CN=Carol,DC=example,DC=com",
                groups=["netops", "helpdesk"],
                is_admin=False,
            )
        )
        saved_id = saved.id
        db.session.expire_all()
        fresh = db.session.get(User, saved_id)
        assert fresh is not None
        assert {row.group_name for row in fresh.ldap_groups} == {"netops", "helpdesk"}
        assert fresh.display_name == "Каролина"
        assert fresh.email == "carol@example.com"
        assert fresh.ldap_dn == "CN=Carol,DC=example,DC=com"
        assert fresh.is_admin is False
        assert fresh.last_login_at is not None


def test_login_post_redirects_and_creates_user(client, app, monkeypatch):
    identity = LdapIdentity(
        username="bob",
        display_name="Боб",
        email="bob@example.com",
        dn="CN=Bob,DC=example,DC=com",
        groups=["bawh-admins"],
        is_admin=True,
    )
    monkeypatch.setattr("app.routes.auth.authenticate", lambda username, password: identity)

    response = client.post(
        "/login?next=/health",
        data={"username": "Bob", "password": "secret"},
    )
    assert response.status_code == 302
    assert _path(response) == "/health"

    again = client.get("/login?next=/health")
    assert again.status_code == 302
    assert _path(again) == "/"

    with app.app_context():
        user = db.session.scalar(select(User).where(User.username == "bob"))
        assert user is not None
        assert user.is_admin is True
        assert user.display_name == "Боб"
        assert {row.group_name for row in user.ldap_groups} == {"bawh-admins"}


def test_login_ignores_external_next(client, monkeypatch):
    identity = LdapIdentity(
        username="bob",
        display_name="Боб",
        email=None,
        dn="CN=Bob,DC=example,DC=com",
        groups=[],
        is_admin=False,
    )
    monkeypatch.setattr("app.routes.auth.authenticate", lambda username, password: identity)

    response = client.post(
        "/login?next=//evil.example/phish",
        data={"username": "bob", "password": "secret"},
    )
    assert response.status_code == 302
    assert "evil.example" not in response.headers["Location"]
    assert _path(response) == "/"


def test_login_failure_shows_message(client, app, monkeypatch):
    monkeypatch.setattr("app.routes.auth.authenticate", lambda username, password: None)
    response = client.post("/login", data={"username": "alice", "password": "nope"})
    assert response.status_code == 200
    assert "Неверное имя или пароль." in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(select(User).where(User.username == "alice")) is None
