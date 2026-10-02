"""Справочник типов действий в системе.

Сами события живут в журналах (script_runs, device_history,
device_account_history). Здесь — только коды и человекочитаемые названия,
чтобы UI и отчёты не размазывали строки по коду.
"""

from app.extensions import db


class ActionKindCode:
    POLL = "poll"
    PING = "ping"
    TRACERT = "tracert"
    COMMAND = "command"
    SCRIPT = "script"
    ACCOUNT_SIGHTING = "account_sighting"


# Стартовый набор справочника — миграция и seed_action_kinds используют одно.
ACTION_KIND_SEED = (
    (ActionKindCode.POLL, "Опрос доступности", "Фоновый ICMP-опрос сектора"),
    (ActionKindCode.PING, "Ping", "Ручная проверка доступности с карточки"),
    (ActionKindCode.TRACERT, "Трассировка", "Трассировка маршрута до устройства"),
    (ActionKindCode.COMMAND, "Команда", "Произвольная команда через PsExec"),
    (ActionKindCode.SCRIPT, "Скрипт", "Запуск скрипта из библиотеки"),
    (ActionKindCode.ACCOUNT_SIGHTING, "Обнаружение УЗ", "Учётка замечена на устройстве"),
)


class ActionKind(db.Model):
    """Строка справочника типов действий."""

    __tablename__ = "action_kinds"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False, unique=True, index=True)
    title = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<ActionKind {self.code}>"


def seed_action_kinds(session=None) -> None:
    """Идемпотентно заполняет справочник (тесты / create_all без миграции)."""
    from sqlalchemy import select

    from app.extensions import db as _db

    bind = session or _db.session
    existing = {kind.code for kind in bind.scalars(select(ActionKind)).all()}
    for code, title, description in ACTION_KIND_SEED:
        if code in existing:
            continue
        bind.add(ActionKind(code=code, title=title, description=description))
