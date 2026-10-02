def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_login_page(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Имя пользователя" in response.get_data(as_text=True)


def test_map_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
