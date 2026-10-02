"""Вход и выход: LDAP или локальный пароль.

Пароль удачного входа шифруется для PsExec-fallback. Одинаковый ответ
на неверный пароль и недоступный каталог не раскрывает наличие учётки.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.services.credential_service import remember_login_password
from app.services.crypto_service import CryptoError, CryptoNotConfigured
from app.services.ldap_service import authenticate as authenticate_ldap
from app.services.ldap_service import upsert_local_user
from app.services.local_auth_service import authenticate_local, ensure_local_admin, local_auth_enabled

bp = Blueprint("auth", __name__)


def _safe_next_url() -> str:
    """Путь после входа: только относительный /..., без //host и схем."""
    target = request.args.get("next", "")
    if isinstance(target, str) and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("devices.map")


def _selected_auth_method() -> str:
    """Способ входа из формы. По умолчанию LDAP."""
    raw = (request.form.get("auth_method") or "ldap").strip().lower()
    if raw == "local" and local_auth_enabled():
        return "local"
    return "ldap"


def _login_template(**extra):
    return render_template(
        "auth/login.html",
        local_auth_enabled=local_auth_enabled(),
        auth_method=extra.get("auth_method", "ldap"),
        local_admin_username=current_app.config.get("LOCAL_ADMIN_USERNAME", ""),
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    # Уже вошедший не гоняет LDAP и не уходит по чужому next.
    if current_user.is_authenticated:
        return redirect(url_for("devices.map"))

    if local_auth_enabled():
        ensure_local_admin()

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        method = _selected_auth_method()

        if method == "local":
            user = authenticate_local(username, password)
            if user is None:
                flash("Неверное имя или пароль.", "danger")
                return _login_template(auth_method="local")
        else:
            identity = authenticate_ldap(username, password)
            if identity is None:
                flash("Неверное имя или пароль.", "danger")
                return _login_template(auth_method="ldap")
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

    return _login_template(auth_method="ldap")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
