"""Проверка, истечёт ли пароль AD за время отпуска.

Письма кадров читаются из служебного IMAP-ящика. В работу берётся тема
«Приближается отпуск сотрудников с ДД.ММ.ГГГГ». Сотрудник ищется в AD по CN:
фамилия и имя, без отчества. Срок отпуска в письме нет — берётся настройка
модуля (по умолчанию 14 календарных дней). Расчёт срока пароля тот же, что
у планового отчёта: pwdLastSet + max_pwd_age. Письмо сотруднику уходит один
раз на пару «логин + дата начала» и не попадает в цикл upcoming/overdue.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select

from app.extensions import db
from app.models.password_expiry import VacationNotice, VacationPasswordCheck
from app.services.password_ad_client import (
    AdUser,
    PasswordAdError,
    compute_days_left,
    find_users_by_short_name,
)
from app.services.password_expiry_settings import (
    get_password_expiry_settings,
    get_smtp_settings,
    get_vacation_check_settings,
    is_user_mail_paused,
)
from app.services.password_mailer import PasswordMailer, PasswordMailerError
from app.services.vacation_imap import (
    InboxMessage,
    VacationImapError,
    close_client,
    fetch_unseen,
    mark_seen,
)
from app.services.vacation_notice_parser import (
    VacationBlock,
    directory_short_name,
    is_vacation_subject,
    parse_vacation_notice,
)
from app.utils import utcnow

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()


class VacationRunInProgressError(RuntimeError):
    """Уже выполняется другой разбор писем об отпусках."""


@dataclass
class VacationCheckRow:
    employee_name: str
    username: str
    email: str
    full_name: str
    match_status: str
    days_left: int | None
    expiry_date: date | None
    vacation_start: date
    vacation_days: int
    expires_during_vacation: bool
    user_notified: bool
    note: str = ""


@dataclass
class VacationRunResult:
    exit_code: int
    messages: int = 0
    employees: int = 0
    risks: int = 0
    notified: int = 0
    unmatched: int = 0
    error: str = ""
    rows: list[VacationCheckRow] = field(default_factory=list)


def password_expires_during_vacation(
    *,
    pwd_last_set,
    password_never_expires: bool,
    max_pwd_age_days: int,
    vacation_start: date,
    vacation_days: int,
) -> tuple[bool, int | None, date | None]:
    """Пароль не доживёт до выхода, если истечёт строго раньше дня возвращения.

    Отпуск из N календарных дней, начавшийся `start`, заканчивается
    включительно в `start + N - 1`. День выхода — `start + N`.
    Уже просроченный пароль и «смена при следующем входе» тоже риск.
    Флаг «пароль не истекает» риском не считается.
    """
    if password_never_expires:
        return False, None, None
    days_left, expiry = compute_days_left(
        pwd_last_set,
        max_pwd_age_days,
        today=vacation_start,
    )
    if pwd_last_set is None or expiry is None:
        return True, days_left, expiry
    return_day = vacation_start + timedelta(days=vacation_days)
    return expiry < return_day, days_left, expiry


def load_recent_vacation_checks(limit: int = 40) -> list[VacationPasswordCheck]:
    return list(
        db.session.scalars(
            select(VacationPasswordCheck)
            .order_by(VacationPasswordCheck.id.desc())
            .limit(limit)
        ).all()
    )


def run_vacation_checks(
    *,
    send_emails: bool = True,
    mode: str = "scheduled",
    today: date | None = None,
    messages: list[InboxMessage] | None = None,
) -> VacationRunResult:
    """Разобрать непрочитанные письма и проверить пароли. Без параллельных прогонов."""
    if not _run_lock.acquire(blocking=False):
        raise VacationRunInProgressError("Проверка отпусков уже выполняется")
    current = today or date.today()
    client = None
    try:
        client, result = _run_unlocked(
            send_emails=send_emails,
            mode=mode,
            today=current,
            messages=messages,
        )
        return result
    except Exception:
        db.session.rollback()
        raise
    finally:
        close_client(client)
        _run_lock.release()


def _run_unlocked(
    *,
    send_emails: bool,
    mode: str,
    today: date,
    messages: list[InboxMessage] | None,
) -> tuple[object, VacationRunResult]:
    settings = get_password_expiry_settings()
    vacation = get_vacation_check_settings()
    mailer = PasswordMailer(get_smtp_settings(), dry_run=not send_emails)
    paused = is_user_mail_paused(today)

    inbox = messages
    client = None
    if inbox is None:
        try:
            client, inbox = fetch_unseen(vacation, readonly=not send_emails)
        except VacationImapError as exc:
            logger.exception("Отпуска: ошибка IMAP")
            _try_alert(mailer, settings.admin_recipients, str(exc), send_emails=send_emails)
            return None, VacationRunResult(exit_code=1, error=str(exc))

    if not inbox:
        logger.info("Отпуска: писем с темой об отпуске нет")
        return client, VacationRunResult(exit_code=0)

    result = VacationRunResult(exit_code=0, messages=0)
    for message in inbox:
        if not is_vacation_subject(message.subject):
            logger.info("Отпуска: пропуск «%s» — тема не про приближающийся отпуск", message.subject)
            continue
        if send_emails and _already_processed(message.message_key):
            if client is not None:
                mark_seen(client, message.uid)
            continue
        result.messages += 1
        try:
            rows, notice_error = _evaluate_message(
                message,
                settings=settings,
                vacation_days=vacation.vacation_days,
                today=today,
                paused=paused,
                send_emails=send_emails,
                mailer=mailer,
                instructions_url=settings.instructions_url,
                admin_recipients=settings.admin_recipients,
            )
        except PasswordAdError as exc:
            logger.exception("Отпуска: ошибка каталога")
            _try_alert(mailer, settings.admin_recipients, str(exc), send_emails=send_emails)
            result.exit_code = 1
            result.error = str(exc)
            return client, result
        result.rows.extend(rows)
        result.employees += len(rows)
        result.risks += sum(1 for row in rows if row.expires_during_vacation)
        result.notified += sum(1 for row in rows if row.user_notified)
        result.unmatched += sum(1 for row in rows if row.match_status != "matched")
        if send_emails:
            _persist_message(message, rows, notice_error, vacation.vacation_days, mode)
            if client is not None:
                mark_seen(client, message.uid)
        elif notice_error and not rows:
            result.error = notice_error

    if not send_emails:
        db.session.rollback()

    logger.info(
        "Отпуска: писем=%s сотрудников=%s риск=%s писем сотрудникам=%s",
        result.messages,
        result.employees,
        result.risks,
        result.notified,
    )
    return client, result


def _evaluate_message(
    message: InboxMessage,
    *,
    settings,
    vacation_days: int,
    today: date,
    paused: bool,
    send_emails: bool,
    mailer: PasswordMailer,
    instructions_url: str,
    admin_recipients: list[str],
) -> tuple[list[VacationCheckRow], str]:
    blocks = parse_vacation_notice(message.body)
    if not blocks:
        error = "В письме не найдена дата отпуска и список сотрудников"
        _send_admin_digest(
            mailer,
            admin_recipients,
            subject=message.subject,
            rows=[],
            error=error,
            send_emails=send_emails,
        )
        return [], error

    resolved: list[tuple[str, VacationBlock, str, list[AdUser]]] = []
    for block in blocks:
        for name in block.names:
            short_name = directory_short_name(name)
            found = find_users_by_short_name(short_name, settings, today=today) if short_name else []
            resolved.append((name, block, short_name, found))

    rows: list[VacationCheckRow] = []
    for name, block, short_name, found in resolved:
        rows.append(
            _check_person(
                name,
                block=block,
                short_name=short_name,
                found=found,
                max_pwd_age_days=settings.max_pwd_age_days,
                vacation_days=vacation_days,
                today=today,
                paused=paused,
                send_emails=send_emails,
                mailer=mailer,
                instructions_url=instructions_url,
            )
        )
    _send_admin_digest(
        mailer,
        admin_recipients,
        subject=message.subject,
        rows=rows,
        error="",
        send_emails=send_emails,
    )
    return rows, ""


def _check_person(
    name: str,
    *,
    block: VacationBlock,
    short_name: str,
    found: list[AdUser],
    max_pwd_age_days: int,
    vacation_days: int,
    today: date,
    paused: bool,
    send_emails: bool,
    mailer: PasswordMailer,
    instructions_url: str,
) -> VacationCheckRow:
    if len(found) != 1:
        if len(found) > 1:
            logins = ", ".join(user.username for user in found)
            note = f"Несколько учёток с CN «{short_name}»: {logins}"
            status = "ambiguous"
        else:
            note = f"В AD нет учётки с CN «{short_name}»" if short_name else "Пустое имя"
            status = "not_found"
        return VacationCheckRow(
            employee_name=name,
            username="",
            email="",
            full_name="",
            match_status=status,
            days_left=None,
            expiry_date=None,
            vacation_start=block.start,
            vacation_days=vacation_days,
            expires_during_vacation=False,
            user_notified=False,
            note=note,
        )
    user = found[0]

    expires, days_left, expiry = password_expires_during_vacation(
        pwd_last_set=user.pwd_last_set,
        password_never_expires=user.password_never_expires,
        max_pwd_age_days=max_pwd_age_days,
        vacation_start=block.start,
        vacation_days=vacation_days,
    )
    note = ""
    if user.password_never_expires:
        note = "Пароль не истекает"
    elif user.pwd_last_set is None:
        note = "Смена пароля при следующем входе"
    notified = False
    vacation_open = today <= block.start + timedelta(days=vacation_days - 1)
    if expires and user.email and vacation_open and not paused:
        already = send_emails and _already_notified(user.username, block.start)
        if not already:
            try:
                _send_user_mail(
                    mailer,
                    user,
                    days_left=days_left,
                    expiry=expiry,
                    vacation_start=block.start,
                    vacation_days=vacation_days,
                    instructions_url=instructions_url,
                )
                notified = True
            except PasswordMailerError:
                logger.exception("Отпуска: не удалось написать %s", user.username)
                note = (note + "; " if note else "") + "Письмо сотруднику не отправлено"
        else:
            note = (note + "; " if note else "") + "Сотруднику уже писали об этом отпуске"
    elif expires and paused:
        note = (note + "; " if note else "") + "Пауза писем пользователям"
    elif expires and not user.email:
        note = (note + "; " if note else "") + "У учётки нет email"
    elif expires and not vacation_open:
        note = (note + "; " if note else "") + "Отпуск уже закончился"

    return VacationCheckRow(
        employee_name=name,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        match_status="matched",
        days_left=days_left,
        expiry_date=expiry,
        vacation_start=block.start,
        vacation_days=vacation_days,
        expires_during_vacation=expires,
        user_notified=notified,
        note=note,
    )


def _send_user_mail(
    mailer: PasswordMailer,
    user: AdUser,
    *,
    days_left: int | None,
    expiry: date | None,
    vacation_start: date,
    vacation_days: int,
    instructions_url: str,
) -> None:
    last_day = vacation_start + timedelta(days=vacation_days - 1)
    html = mailer.render_vacation_user(
        full_name=user.full_name,
        username=user.username,
        days_left=days_left,
        expiry_date=_fmt_date(expiry),
        vacation_start=_fmt_date(vacation_start),
        vacation_end=_fmt_date(last_day),
        must_change=user.pwd_last_set is None and not user.password_never_expires,
        already_expired=days_left is not None and days_left <= 0,
        instructions_url=instructions_url,
    )
    mailer.send_html([user.email], "Смените пароль до отпуска", html)


def _send_admin_digest(
    mailer: PasswordMailer,
    recipients: list[str],
    *,
    subject: str,
    rows: list[VacationCheckRow],
    error: str,
    send_emails: bool,
) -> None:
    if not recipients:
        logger.info("Отпуска: получатели админ-отчёта не заданы — сводка не отправлена")
        return
    title = subject or "Письмо отдела кадров"
    html = mailer.render_vacation_admin(
        {
            "mail_subject": title,
            "error": error,
            "rows": [
                {
                    "employee_name": row.employee_name,
                    "full_name": row.full_name or row.employee_name,
                    "username": row.username or "—",
                    "vacation_start": _fmt_date(row.vacation_start),
                    "expiry_date": _fmt_date(row.expiry_date),
                    "outcome": _outcome(row),
                    "note": row.note,
                }
                for row in rows
            ],
        }
    )
    try:
        mailer.send_html(recipients, f"Отпуск и срок пароля — {title}", html)
    except PasswordMailerError:
        logger.exception("Отпуска: не удалось отправить сводку администраторам")
        if send_emails:
            _try_alert(
                mailer,
                recipients,
                "Не удалось отправить сводку проверки паролей перед отпуском.",
                send_emails=True,
            )


def _persist_message(
    message: InboxMessage,
    rows: list[VacationCheckRow],
    error: str,
    vacation_days: int,
    mode: str,
) -> None:
    risks = sum(1 for row in rows if row.expires_during_vacation)
    unmatched = sum(1 for row in rows if row.match_status != "matched")
    if error:
        status = "error"
    elif unmatched:
        status = "partial"
    else:
        status = "ok"
    starts = [row.vacation_start for row in rows]
    notice = VacationNotice(
        message_key=message.message_key,
        subject=message.subject,
        vacation_start=min(starts) if starts else None,
        vacation_days=vacation_days,
        employee_lines="\n".join(row.employee_name for row in rows),
        status=status,
        error=error,
        employees_count=len(rows),
        risk_count=risks,
        unmatched_count=unmatched,
        mode=mode,
        created_at=utcnow(),
    )
    db.session.add(notice)
    db.session.flush()
    for row in rows:
        db.session.add(
            VacationPasswordCheck(
                notice_id=notice.id,
                employee_name=row.employee_name,
                username=row.username,
                email=row.email,
                full_name=row.full_name,
                match_status=row.match_status,
                days_left=row.days_left,
                expiry_date=row.expiry_date,
                vacation_start=row.vacation_start,
                vacation_days=row.vacation_days,
                expires_during_vacation=row.expires_during_vacation,
                user_notified=row.user_notified,
                note=row.note,
                created_at=utcnow(),
            )
        )
    db.session.commit()


def _already_processed(message_key: str) -> bool:
    existing = db.session.scalar(
        select(VacationNotice.id).where(VacationNotice.message_key == message_key).limit(1)
    )
    return existing is not None


def _already_notified(username: str, vacation_start: date) -> bool:
    existing = db.session.scalar(
        select(VacationPasswordCheck.id)
        .where(
            VacationPasswordCheck.username == username,
            VacationPasswordCheck.vacation_start == vacation_start,
            VacationPasswordCheck.user_notified.is_(True),
        )
        .limit(1)
    )
    return existing is not None


def _outcome(row: VacationCheckRow) -> str:
    if row.match_status == "not_found":
        return "Не найден в AD"
    if row.match_status == "ambiguous":
        return "Несколько совпадений"
    if row.expires_during_vacation:
        return "Истечёт за отпуск"
    return "Пароль доживёт до выхода"


def _fmt_date(value: date | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y")


def _try_alert(
    mailer: PasswordMailer,
    recipients: list[str],
    message: str,
    *,
    send_emails: bool,
) -> None:
    if not send_emails or not recipients:
        logger.critical("Отпуска: алерт не отправлен: %s", message)
        return
    try:
        mailer.send_alert(recipients, message)
    except PasswordMailerError:
        logger.critical("Отпуска: SMTP недоступен, алерт не отправлен: %s", message)
