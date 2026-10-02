"""Библиотека скриптов и журнал запусков.

script_runs — единый журнал и для скриптов, и для кнопок
Ping / Tracert / произвольной команды. Так страница лога одна,
а не четыре разных механизма.

run_type говорит, что именно запускали. script_id пустой,
если это не скрипт из библиотеки.

Групповой запуск (сектор целиком) пишет несколько строк
с одним batch_id, по строке на устройство.
"""

from app.extensions import db
from app.models.base import TimestampMixin
from app.utils import utcnow


class RunType:
    PING = "ping"
    TRACERT = "tracert"
    COMMAND = "command"
    SCRIPT = "script"


class RunStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class Script(TimestampMixin, db.Model):
    __tablename__ = "scripts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    # windows или linux. Сейчас удалённый запуск рассчитан на Windows (PsExec).
    target_os = db.Column(db.String(32), nullable=False, default="windows")
    # powershell, cmd или bash
    interpreter = db.Column(db.String(32), nullable=False, default="powershell")
    # db — текст в колонке content; filesystem — файл в SCRIPT_LIBRARY_DIR
    storage = db.Column(db.String(16), nullable=False, default="db")
    file_path = db.Column(db.String(512), nullable=True)
    content = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_by = db.relationship("User")
    runs = db.relationship("ScriptRun", back_populates="script")

    def __repr__(self) -> str:
        return f"<Script {self.name}>"


class ScriptRun(db.Model):
    __tablename__ = "script_runs"

    id = db.Column(db.Integer, primary_key=True)
    script_id = db.Column(
        db.Integer,
        db.ForeignKey("scripts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    device_id = db.Column(
        db.Integer,
        db.ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    batch_id = db.Column(db.String(36), nullable=True, index=True)
    run_type = db.Column(db.String(16), nullable=False)
    # Текст команды без секретов. Пароль PsExec сюда писать нельзя.
    command_text = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(16), nullable=False, default=RunStatus.PENDING, index=True)
    exit_code = db.Column(db.Integer, nullable=True)
    log_text = db.Column(db.Text, nullable=False, default="")
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    script = db.relationship("Script", back_populates="runs")
    device = db.relationship("Device")
    user = db.relationship("User")
