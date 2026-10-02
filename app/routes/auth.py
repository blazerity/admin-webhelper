"""Вход и выход.

Пароль здесь не проверяется и не сохраняется. Маршрут только забирает
форму, спрашивает каталог через ldap_service и открывает сессию Flask-Login.
Одинаковый ответ на неверный пароль и на недоступный каталог не даёт
понять по ошибке, есть ли такая учётка.

Имена эндпоинтов нельзя менять: на них ссылается меню и login_view.

- login    GET/POST /login
- logout   POST /logout
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.services.ldap_service import authenticate, upsert_local_user

bp = Blueprint("auth", __name__)


def _safe_next_url() -> str:
    """Куда вернуть человека после входа.

    next приходит ссылкой со страницы, которая потребовала вход
    (?next=/sectors/1). Берём его, только если это путь нашего сайта.
    «//чужой.host» браузер откроет на другом хосте без схемы.
    Адрес со схемой не начинается с одного слэша и тоже отбрасывается.
    """
    target = request.args.get("next", "")
    if isinstance(target, str) and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("devices.map")


@bp.route("/login", methods=["GET", "POST"])
def login():
    # Повторный заход не должен заново гонять пароль через LDAP
    # и не должен уводить уже вошедшего по чужому next.
    if current_user.is_authenticated:
        return redirect(url_for("devices.map"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        identity = authenticate(username, password)
        if identity is None:
            flash("Неверное имя или пароль.", "danger")
            return render_template("auth/login.html")
        # Сессия хранит id локальной строки. Пока её нет, login_user не к чему привязаться.
        user = upsert_local_user(identity)
        login_user(user)
        return redirect(_safe_next_url())

    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
