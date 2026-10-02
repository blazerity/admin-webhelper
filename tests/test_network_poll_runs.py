"""Журнал прогонов опроса и принудительный запуск из Параметров."""

from app.extensions import db
from app.models import DeviceStatus, NetworkPollRun, Sector, SectorRange
from app.services.ping_service import (
    PollInProgressError,
    PingResult,
    load_recent_poll_runs,
    poll_all_sectors,
    run_network_poll,
)


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _sector_with_range(app):
    with app.app_context():
        sector = Sector(name="lab", description="")
        db.session.add(sector)
        db.session.flush()
        db.session.add(SectorRange(sector_id=sector.id, cidr="10.9.9.1"))
        db.session.commit()


def _always_offline(monkeypatch):
    monkeypatch.setattr(
        "app.services.ping_service.ping_host",
        lambda ip, timeout_s=1, **kwargs: PingResult(
            DeviceStatus.OFFLINE, None, "timeout"
        ),
    )


def test_run_network_poll_persists_stats(app, monkeypatch):
    _sector_with_range(app)
    _always_offline(monkeypatch)
    with app.app_context():
        stats = run_network_poll(mode="cli")
        assert stats["scanned"] == 1
        runs = load_recent_poll_runs()
        assert len(runs) == 1
        assert runs[0].mode == "cli"
        assert runs[0].finished_at is not None
        assert runs[0].error == ""
        assert runs[0].scanned == stats["scanned"]


def test_poll_all_sectors_does_not_write_run(app, monkeypatch):
    _sector_with_range(app)
    _always_offline(monkeypatch)
    with app.app_context():
        poll_all_sectors()
        assert NetworkPollRun.query.count() == 0


def test_parallel_poll_raises(app, monkeypatch):
    _sector_with_range(app)
    _always_offline(monkeypatch)
    with app.app_context():
        started = NetworkPollRun(mode="scheduled")
        db.session.add(started)
        db.session.commit()
        try:
            run_network_poll(mode="manual")
            assert False, "ожидали PollInProgressError"
        except PollInProgressError:
            pass


def test_admin_settings_shows_runs_and_force_button(client, app, admin_id, monkeypatch):
    _sector_with_range(app)
    _always_offline(monkeypatch)
    _login(client, admin_id)
    with app.app_context():
        run_network_poll(mode="scheduled")

    page = client.get("/admin/settings")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Последние прогоны" in text
    assert "Запустить опрос сейчас" in text
    assert "scheduled" in text
    assert "/admin/poll-run" in text


def test_admin_can_force_poll(client, app, admin_id, monkeypatch):
    _sector_with_range(app)
    _always_offline(monkeypatch)
    _login(client, admin_id)
    response = client.post("/admin/poll-run", follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Опрос завершён" in body
    with app.app_context():
        run = NetworkPollRun.query.one()
        assert run.mode == "manual"
        assert run.finished_at is not None


def test_force_poll_requires_admin(client, alice_id):
    _login(client, alice_id)
    assert client.post("/admin/poll-run").status_code == 403
