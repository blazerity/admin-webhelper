"""Старые адреса настроек. Канон — /admin/…

Сектора, скрипты, УЗ и действия долго жили в корне сайта, хотя в меню
это разделы настроек. Закладки и письма со старыми ссылками попадают сюда
и уходят на новый путь тем же методом (308).
"""

from flask import Blueprint, redirect, request

bp = Blueprint("legacy", __name__)


def _under_admin(prefix: str, rest: str):
    tail = (rest or "").strip("/")
    url = prefix if not tail else f"{prefix}/{tail}"
    if request.query_string:
        url = f"{url}?{request.query_string.decode()}"
    return redirect(url, code=308)


@bp.route("/scripts", defaults={"rest": ""}, strict_slashes=False)
@bp.route("/scripts/<path:rest>", strict_slashes=False)
def scripts_moved(rest: str):
    return _under_admin("/admin/scripts", rest)


@bp.route("/sectors", defaults={"rest": ""}, strict_slashes=False)
@bp.route("/sectors/<path:rest>", strict_slashes=False)
def sectors_moved(rest: str):
    return _under_admin("/admin/sectors", rest)


@bp.route("/accounts", defaults={"rest": ""}, strict_slashes=False)
@bp.route("/accounts/<path:rest>", strict_slashes=False)
def accounts_moved(rest: str):
    return _under_admin("/admin/accounts", rest)


@bp.route("/actions", defaults={"rest": ""}, strict_slashes=False)
@bp.route("/actions/<path:rest>", strict_slashes=False)
def actions_moved(rest: str):
    return _under_admin("/admin/actions", rest)


@bp.route("/password-expiry/settings", methods=["GET", "POST"])
def ldap_settings_moved():
    return _under_admin("/admin/settings/ldap", "")


@bp.route("/password-expiry/report-settings", methods=["GET", "POST"])
def password_report_settings_moved():
    return _under_admin("/admin/settings/passwords", "")
