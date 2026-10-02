"""Параметры: учётка WMI для опроса сохраняется только администратором."""


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_discovery_settings_require_admin(client, alice_id):
    _login(client, alice_id)
    assert client.get("/admin/settings").status_code == 403
    assert (
        client.post(
            "/admin/settings",
            data={
                "form": "discovery",
                "discovery_username": "svc",
                "discovery_domain": "CORP",
                "discovery_password": "secret",
            },
        ).status_code
        == 403
    )


def test_admin_can_save_and_see_discovery_account(client, admin_id):
    _login(client, admin_id)
    page = client.get("/admin/settings")
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Учётка WMI (опрос)" in text
    assert 'name="form" value="discovery"' in text
    assert "secret-password" not in text

    response = client.post(
        "/admin/settings",
        data={
            "form": "discovery",
            "discovery_username": "wmisvc",
            "discovery_domain": "CORP",
            "discovery_password": "WmiSecret!",
        },
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Учётка WMI для опроса сохранена" in body
    assert 'value="wmisvc"' in body
    assert 'value="CORP"' in body
    assert "WmiSecret!" not in body
    assert "задан, оставьте пустым чтобы не менять" in body


def test_admin_can_clear_discovery_account(client, admin_id):
    _login(client, admin_id)
    client.post(
        "/admin/settings",
        data={
            "form": "discovery",
            "discovery_username": "wmisvc",
            "discovery_domain": "CORP",
            "discovery_password": "WmiSecret!",
        },
    )
    response = client.post(
        "/admin/settings",
        data={
            "form": "discovery",
            "discovery_username": "",
            "discovery_domain": "",
            "discovery_password": "",
        },
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert "Учётка WMI очищена" in body
    assert 'id="discovery_username" name="discovery_username" value=""' in body or (
        'name="discovery_username" value=""' in body
    )
