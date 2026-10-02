"""Смоук платформы: systemd-юниты и probe /health для deploy.

Не дублирует product-тесты A3 (summary/presets/health-сервис) и A4 (authz).
Проверяет, что артефакты install Debian 12 остаются согласованными с W1.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


@pytest.mark.parametrize(
    ("filename", "needle"),
    [
        ("bawh-web.service", "gunicorn"),
        ("bawh-web.service", "wsgi:app"),
        ("bawh-scheduler.service", "app.scheduler_worker"),
        ("bawh-web.service", "EnvironmentFile=/opt/bawh/.env"),
        ("bawh-scheduler.service", "EnvironmentFile=/opt/bawh/.env"),
    ],
)
def test_deploy_unit_files_keep_expected_exec(filename, needle):
    text = (DEPLOY / filename).read_text(encoding="utf-8")
    assert needle in text


def test_install_script_still_probes_health():
    script = (DEPLOY / "install-debian12.sh").read_text(encoding="utf-8")
    assert "wait_for_health" in script
    assert "/health" in script
    assert "bawh-web" in script and "bawh-scheduler" in script


def test_health_probe_unauthenticated(client):
    """Install и Nginx ждут {"status":"ok"} без сессии."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
