"""Страница обновления: только администратор, кнопки вызывают сервис."""

from app.services.update_service import UpdateError


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def _root(app, tmp_path):
    root = tmp_path / "bawh"
    root.mkdir()
    app.config["PROJECT_ROOT"] = str(root)
    app.config["GIT_REMOTE_URL"] = "https://example.com/org/bAWH.git"
    app.config["GIT_BRANCH"] = "main"
    app.config["UPDATE_RESTART"] = False
    return root


def test_updates_require_login(client):
    response = client.get("/admin/updates")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_updates_forbidden_for_alice(client, alice_id):
    _login(client, alice_id)
    assert client.get("/admin/updates").status_code == 403
    assert client.post("/admin/updates", data={"form": "update"}).status_code == 403
    assert client.post("/admin/updates", data={"form": "rollback", "backup_id": "x"}).status_code == 403


def test_admin_sees_update_and_rollback_controls(client, app, admin_id, tmp_path):
    _root(app, tmp_path)
    _login(client, admin_id)
    page = client.get("/admin/updates")
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Обновить из git" in text
    assert "Резервные копии" in text
    assert "<th>Версия</th>" in text
    assert "<th>Зачем</th>" not in text
    assert "Откатить" not in text

    settings = client.get("/admin/settings")
    text = settings.get_data(as_text=True)
    assert "Открыть обновления" in text
    assert "Sudo-пользователь" in text
    assert 'name="form" value="update"' in text


def test_admin_can_save_update_sudo_user(client, admin_id):
    _login(client, admin_id)
    response = client.post(
        "/admin/settings",
        data={"form": "update", "update_sudo_user": "root"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Sudo-пользователь для перезапуска служб: root" in body
    assert 'id="update_sudo_user" name="update_sudo_user" value="root"' in body or (
        'name="update_sudo_user" value="root"' in body
    )

    cleared = client.post(
        "/admin/settings",
        data={"form": "update", "update_sudo_user": ""},
        follow_redirects=True,
    )
    assert "Sudo-пользователь очищен" in cleared.get_data(as_text=True)


def test_update_button_starts_the_job(client, app, admin_id, tmp_path, monkeypatch):
    _root(app, tmp_path)
    started = {}

    def begin():
        started["yes"] = True

    monkeypatch.setattr("app.routes.admin.begin_update", begin)
    _login(client, admin_id)
    response = client.post("/admin/updates", data={"form": "update"}, follow_redirects=True)
    assert started["yes"] is True
    assert "Обновление запущено" in response.get_data(as_text=True)


def test_bad_backup_id_is_rejected(client, app, admin_id, tmp_path):
    _root(app, tmp_path)
    _login(client, admin_id)
    response = client.post(
        "/admin/updates",
        data={"form": "rollback", "backup_id": "../.env"},
        follow_redirects=True,
    )
    assert "Некорректный идентификатор" in response.get_data(as_text=True)


def test_service_error_is_shown(client, app, admin_id, tmp_path, monkeypatch):
    _root(app, tmp_path)

    def begin():
        raise UpdateError("Репозиторий недоступен.")

    monkeypatch.setattr("app.routes.admin.begin_update", begin)
    _login(client, admin_id)
    response = client.post("/admin/updates", data={"form": "update"}, follow_redirects=True)
    assert "Репозиторий недоступен" in response.get_data(as_text=True)
