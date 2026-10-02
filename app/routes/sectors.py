"""CRUD секторов.

Эндпоинты:
- list_sectors   GET  /sectors/
- new_sector     GET  /sectors/new
- create_sector  POST /sectors/
- detail         GET  /sectors/<sector_id>
- edit_sector    GET  /sectors/<sector_id>/edit
- update_sector  POST /sectors/<sector_id>
- delete_sector  POST /sectors/<sector_id>/delete

Кто что видит, решает app.authz. Здесь только форма и ответы.
Создание и правка доступны администратору. Чужой или несуществующий
сектор на карточке — 404/403 из get_visible_sector_or_404.
Правка несуществующего id — 404 ещё до сохранения.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.authz import accessible_sectors, admin_required, get_visible_sector_or_404
from app.extensions import db
from app.models import Sector, SectorRange
from app.services.net_utils import NetworkInputError
from app.services.sector_service import SectorError
from app.services.sector_service import delete_sector as remove_sector
from app.services.sector_service import save_sector

bp = Blueprint("sectors", __name__, url_prefix="/sectors")


@bp.get("/")
@login_required
def list_sectors():
    sectors = accessible_sectors(current_user)
    if sectors:
        # ranges нужны для |length в шаблоне — без N+1 lazy-load.
        loaded = (
            Sector.query.options(selectinload(Sector.ranges))
            .filter(Sector.id.in_([sector.id for sector in sectors]))
            .all()
        )
        by_id = {sector.id: sector for sector in loaded}
        sectors = [by_id[sector.id] for sector in sectors if sector.id in by_id]
    return render_template(
        "sectors/list.html",
        sectors=sectors,
    )


@bp.get("/new")
@admin_required
def new_sector():
    return render_template(
        "sectors/form.html",
        sector=None,
        access_users="",
        access_groups="",
    )


@bp.post("/")
@admin_required
def create_sector():
    return _save_from_form(None)


@bp.get("/<int:sector_id>")
@login_required
def detail(sector_id: int):
    sector = get_visible_sector_or_404(sector_id)
    return render_template("sectors/detail.html", sector=sector)


@bp.get("/<int:sector_id>/edit")
@admin_required
def edit_sector(sector_id: int):
    sector = _existing_sector(sector_id)
    access_users, access_groups = _access_text(sector)
    return render_template(
        "sectors/form.html",
        sector=sector,
        access_users=access_users,
        access_groups=access_groups,
    )


@bp.post("/<int:sector_id>")
@admin_required
def update_sector(sector_id: int):
    _existing_sector(sector_id)
    return _save_from_form(sector_id)


@bp.post("/<int:sector_id>/delete")
@admin_required
def delete_sector(sector_id: int):
    sector = _existing_sector(sector_id)
    name = sector.name
    try:
        remove_sector(sector_id)
    except SectorError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("sectors.list_sectors"))
    flash(f"Сектор «{name}» удалён.", "success")
    return redirect(url_for("sectors.list_sectors"))


def _existing_sector(sector_id: int) -> Sector:
    sector = db.session.get(Sector, sector_id)
    if sector is None:
        abort(404)
    return sector


def _save_from_form(sector_id: int | None):
    name, description, ranges_text, access_users, access_groups = _form_values()
    try:
        sector = save_sector(
            sector_id,
            name,
            description,
            ranges_text,
            access_users,
            access_groups,
        )
    except (SectorError, NetworkInputError) as exc:
        # Откат на случай, если сервис успел добавить объект в сессию.
        # Черновик собираем после отката и в сессию не кладём,
        # иначе следующий commit записал бы недоделанный сектор.
        db.session.rollback()
        flash(str(exc), "danger")
        return render_template(
            "sectors/form.html",
            sector=_unsaved_sector(name, description, ranges_text, sector_id),
            access_users=access_users,
            access_groups=access_groups,
        )
    if sector_id is None:
        flash(f"Сектор «{sector.name}» создан.", "success")
    else:
        flash(f"Сектор «{sector.name}» сохранён.", "success")
    return redirect(url_for("sectors.detail", sector_id=sector.id))


def _form_values() -> tuple[str, str, str, str, str]:
    return (
        request.form.get("name", ""),
        request.form.get("description", ""),
        request.form.get("ranges", ""),
        request.form.get("access_users", ""),
        request.form.get("access_groups", ""),
    )


def _unsaved_sector(
    name: str,
    description: str,
    ranges_text: str,
    sector_id: int | None,
) -> Sector:
    """Объект только для повторного показа формы. В сессию не добавлять."""
    draft = Sector(name=(name or "").strip(), description=description or "")
    if sector_id is not None:
        draft.id = sector_id
    for line in (ranges_text or "").splitlines():
        text = line.strip()
        if text:
            draft.ranges.append(SectorRange(cidr=text))
    return draft


def _access_text(sector: Sector) -> tuple[str, str]:
    rules = sorted(sector.access_rules, key=lambda rule: rule.id or 0)
    users = [rule.subject_name for rule in rules if rule.subject_type == "user"]
    groups = [rule.subject_name for rule in rules if rule.subject_type == "group"]
    return ", ".join(users), ", ".join(groups)
