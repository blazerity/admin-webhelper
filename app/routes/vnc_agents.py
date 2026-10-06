"""Раздача MSI TightVNC с сервера bAWH (LAN, без логина).

Файлы лежат в ``vnc-agents/``. Псевдонимы ``64bit.msi`` / ``32bit.msi`` нужны
скрипту установки: SYSTEM на ПК не ходит в форму логина.
"""

from flask import Blueprint, abort, send_file

from app.services.tightvnc_install_script import resolve_vnc_agent_file

bp = Blueprint("vnc_agents", __name__)


@bp.get("/vnc-agents/<name>")
def download_vnc_agent(name: str):
    path = resolve_vnc_agent_file(name)
    if path is None:
        abort(404)
    return send_file(
        path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=path.name,
        max_age=0,
        conditional=True,
    )
