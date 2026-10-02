"""Health планировщика опроса для /admin/settings."""

from datetime import timedelta

from app.extensions import db
from app.models import NetworkPollRun
from app.services.network_summary_service import get_scheduler_health, stale_after_seconds
from app.services.settings_service import set_poll_interval_seconds
from app.utils import utcnow


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_health_without_poll_is_stale(app):
    with app.app_context():
        health = get_scheduler_health()
        assert health["last_success_at"] is None
        assert health["last_run"] is None
        assert health["stale"] is True
        assert health["stale_after_seconds"] == max(
            stale_after_seconds(), 600
        )


def test_health_fresh_success_not_stale(app):
    with app.app_context():
        set_poll_interval_seconds(300)
        finished = utcnow() - timedelta(seconds=60)
        run = NetworkPollRun(
            started_at=finished - timedelta(seconds=10),
            finished_at=finished,
            mode="scheduled",
            scanned=1,
            online=1,
            offline=0,
            errors=0,
            error="",
        )
        db.session.add(run)
        db.session.commit()

        health = get_scheduler_health()
        assert health["stale"] is False
        assert health["stale_after_seconds"] == 600
        assert health["last_success_at"] is not None
        assert health["last_run"]["id"] == run.id
        assert health["last_run"]["mode"] == "scheduled"


def test_health_stale_after_threshold(app):
    with app.app_context():
        set_poll_interval_seconds(300)
        finished = utcnow() - timedelta(seconds=601)
        run = NetworkPollRun(
            started_at=finished - timedelta(seconds=5),
            finished_at=finished,
            mode="manual",
            scanned=2,
            online=0,
            offline=2,
            errors=0,
            error="",
        )
        db.session.add(run)
        db.session.commit()

        health = get_scheduler_health()
        assert health["stale"] is True
        assert health["stale_after_seconds"] == 600
        assert health["last_run"]["mode"] == "manual"


def test_health_uses_poll_interval_times_two(app):
    with app.app_context():
        set_poll_interval_seconds(400)
        # max(800, 600) = 800
        finished = utcnow() - timedelta(seconds=801)
        run = NetworkPollRun(
            started_at=finished - timedelta(seconds=5),
            finished_at=finished,
            mode="cli",
            scanned=1,
            online=1,
            offline=0,
            errors=0,
            error="",
        )
        db.session.add(run)
        db.session.commit()

        health = get_scheduler_health()
        assert health["stale_after_seconds"] == 800
        assert health["stale"] is True


def test_failed_poll_does_not_count_as_success(app):
    with app.app_context():
        finished = utcnow() - timedelta(seconds=30)
        db.session.add(
            NetworkPollRun(
                started_at=finished - timedelta(seconds=5),
                finished_at=finished,
                mode="scheduled",
                scanned=0,
                online=0,
                offline=0,
                errors=1,
                error="boom",
            )
        )
        db.session.commit()
        health = get_scheduler_health()
        assert health["last_success_at"] is None
        assert health["stale"] is True
        assert health["last_run"]["error"] == "boom"


def test_admin_settings_passes_scheduler_health(client, app, admin_id):
    with app.app_context():
        finished = utcnow() - timedelta(seconds=10)
        db.session.add(
            NetworkPollRun(
                started_at=finished - timedelta(seconds=2),
                finished_at=finished,
                mode="scheduled",
                scanned=1,
                online=1,
                offline=0,
                errors=0,
                error="",
            )
        )
        db.session.commit()

    _login(client, admin_id)
    # Шаблон A2 ещё может не рендерить блок; важно, что route не падает
    # и context собирается (render успешен).
    page = client.get("/admin/settings")
    assert page.status_code == 200
