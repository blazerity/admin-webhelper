"""W4: /api/v1 aliases и CSV export."""

from flask import g, has_app_context


def _login(client, user_id):
    if has_app_context():
        g.pop("_login_user", None)
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_api_v1_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_api_v1_summary_requires_login(client):
    assert client.get("/api/v1/network/summary").status_code in {302, 401}


def test_api_v1_summary_ok(client, admin_id):
    _login(client, admin_id)
    response = client.get("/api/v1/network/summary")
    assert response.status_code == 200
    assert "devices_total" in response.get_json()


def test_api_v1_map_status(client, admin_id):
    _login(client, admin_id)
    response = client.get("/api/v1/map/status")
    assert response.status_code == 200
    assert "sectors" in response.get_json()


def test_api_v1_notifications(client, admin_id):
    _login(client, admin_id)
    response = client.get("/api/v1/notifications")
    assert response.status_code == 200
    data = response.get_json()
    assert "items" in data
    assert "unread" in data


def test_api_v1_search_suggest_matches_legacy(client, admin_id):
    """Thin alias: тот же JSON-массив, что /search/suggest."""
    _login(client, admin_id)
    legacy = client.get("/search/suggest?q=ab")
    alias = client.get("/api/v1/search/suggest?q=ab")
    assert legacy.status_code == 200
    assert alias.status_code == 200
    assert alias.get_json() == legacy.get_json()
    assert isinstance(alias.get_json(), list)


def test_accounts_csv(client, admin_id):
    _login(client, admin_id)
    response = client.get("/accounts/export.csv")
    assert response.status_code == 200
    assert "text/csv" in response.content_type
    assert "id,domain,username" in response.get_data(as_text=True).splitlines()[0]


def test_actions_csv(client, admin_id):
    _login(client, admin_id)
    response = client.get("/actions/export.csv")
    assert response.status_code == 200
    assert "kind_code" in response.get_data(as_text=True).splitlines()[0]


def test_poll_runs_csv_admin(client, admin_id):
    _login(client, admin_id)
    response = client.get("/admin/poll-runs/export.csv")
    assert response.status_code == 200
    assert "scanned" in response.get_data(as_text=True).splitlines()[0]


def test_poll_runs_csv_forbidden_for_user(client, alice_id):
    _login(client, alice_id)
    assert client.get("/admin/poll-runs/export.csv").status_code == 403


def test_vendor_bootstrap_served(client):
    css = client.get("/static/vendor/bootstrap/bootstrap.min.css")
    js = client.get("/static/vendor/bootstrap/bootstrap.bundle.min.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert len(css.data) > 1000
