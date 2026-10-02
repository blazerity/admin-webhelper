"""Вход и выход через LDAP.

Пароль удачного входа шифруется для PsExec-fallback. Одинаковый ответ
на неверный пароль и недоступный каталог не раскрывает наличие учётки.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.services.credential_service import remember_login_password
from app.services.crypto_service import CryptoError, CryptoNotConfigured
from app.services.ldap_service import authenticate, upsert_local_user

bp = Blueprint("auth", __name__)


def _safe_next_url() -> str:
    """Путь после входа: только относительный /..., без //host и схем."""
    target = request.args.get("next", "")
    if isinstance(target, str) and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("devices.map")


@bp.route("/login", methods=["GET", "POST"])
def login():
    # Уже вошедший не гоняет LDAP и не уходит по чужому next.
    if current_user.is_authenticated:
        return redirect(url_for("devices.map"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        identity = authenticate(username, password)
        if identity is None:
            flash("Неверное имя или пароль.", "danger")
            return render_template("auth/login.html")
        # Сессия Flask-Login хранит id локальной строки.
        user = upsert_local_user(identity)
        try:
            remember_login_password(user.id, password)
        except (CryptoNotConfigured, CryptoError):
            current_app.logger.warning(
                "Пароль входа пользователя id=%s не сохранён для PsExec.",
                user.id,
            )
        login_user(user)
        return redirect(_safe_next_url())

    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
