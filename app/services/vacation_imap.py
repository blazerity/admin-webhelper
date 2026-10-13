"""Чтение непрочитанных писем кадров из служебного ящика (IMAP).

Пароль ящика — в настройках модуля, не в .env. После успешной обработки
письмо помечается прочитанным, чтобы следующий прогон его не брал.
"""

from __future__ import annotations

import base64
import email
import imaplib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from hashlib import sha256

from app.services.password_expiry_settings import VacationCheckSettings
from app.services.vacation_notice_parser import html_to_text

logger = logging.getLogger(__name__)

_MAX_MESSAGES = 50


class VacationImapError(RuntimeError):
    """Ящик недоступен или ответ IMAP не разобрать."""


@dataclass(frozen=True)
class InboxMessage:
    """Одно письмо: ключ идемпотентности, текст и UID для флага \\Seen."""

    message_key: str
    uid: str
    subject: str
    body: str
    received_at: datetime | None


def fetch_unseen(
    settings: VacationCheckSettings,
    *,
    readonly: bool = False,
) -> tuple[imaplib.IMAP4, list[InboxMessage]]:
    """Открыть ящик и забрать непрочитанные письма. Соединение закрывает вызывающий."""
    if not settings.imap_configured:
        raise VacationImapError(
            "IMAP не настроен: укажите сервер, логин и пароль "
            "в Настройки → Отчёт о паролях"
        )
    client = _connect(settings)
    try:
        folder = mailbox_arg(settings.imap_folder or "INBOX")
        status, _ = client.select(folder, readonly=readonly)
        if status != "OK":
            raise VacationImapError(f"Не удалось открыть папку «{settings.imap_folder}»")
        status, data = client.uid("SEARCH", "UNSEEN")
        if status != "OK":
            raise VacationImapError("IMAP не вернул список непрочитанных писем")
        uids = [item for item in (data[0] or b"").split() if item][:_MAX_MESSAGES]
        messages: list[InboxMessage] = []
        for uid in uids:
            messages.append(_fetch_uid(client, uid.decode("ascii", errors="replace")))
        return client, messages
    except VacationImapError:
        _logout(client)
        raise
    except (imaplib.IMAP4.error, OSError) as exc:
        _logout(client)
        raise VacationImapError(f"Ошибка IMAP ({settings.imap_host}): {exc}") from exc


def mark_seen(client: imaplib.IMAP4, uid: str) -> None:
    try:
        client.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.warning("IMAP: не удалось пометить письмо %s прочитанным: %s", uid, exc)


def close_client(client: imaplib.IMAP4 | None) -> None:
    _logout(client)


def test_imap_connection(settings: VacationCheckSettings) -> str:
    """Проверить логин и выбор папки, не читая письма."""
    client = _connect(settings)
    try:
        folder = mailbox_arg(settings.imap_folder or "INBOX")
        status, data = client.select(folder, readonly=True)
        if status != "OK":
            raise VacationImapError(f"Нет доступа к папке «{settings.imap_folder}»")
        count = (data[0] or b"0").decode("ascii", errors="replace") if data else "0"
        return f"IMAP OK: {settings.imap_host}:{settings.imap_port}, писем в папке {count}"
    except VacationImapError:
        raise
    except (imaplib.IMAP4.error, OSError) as exc:
        raise VacationImapError(f"IMAP недоступен ({settings.imap_host}): {exc}") from exc
    finally:
        _logout(client)


def mailbox_arg(name: str) -> str:
    """Имя папки в modified UTF-7; кавычки, если внутри есть пробел."""
    encoded = encode_mailbox(name)
    if any(char in encoded for char in ' "'):
        escaped = encoded.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return encoded


def encode_mailbox(name: str) -> str:
    """Modified UTF-7 (RFC 3501) для не-ASCII имён папок."""
    if not name:
        return "INBOX"
    if name.isascii():
        return name
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        raw = "".join(buf).encode("utf-16-be")
        token = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + token + "-")
        buf.clear()

    for char in name:
        if char == "&":
            flush()
            out.append("&-")
        elif ord(char) < 0x20 or ord(char) > 0x7E:
            buf.append(char)
        else:
            flush()
            out.append(char)
    flush()
    return "".join(out)


def _connect(settings: VacationCheckSettings) -> imaplib.IMAP4:
    try:
        if settings.imap_use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                settings.imap_host,
                settings.imap_port,
                timeout=30,
            )
        else:
            client = imaplib.IMAP4(settings.imap_host, settings.imap_port, timeout=30)
        client.login(settings.imap_user, settings.imap_password)
        return client
    except (imaplib.IMAP4.error, OSError) as exc:
        raise VacationImapError(
            f"Не удалось войти в IMAP {settings.imap_host}:{settings.imap_port}: {exc}"
        ) from exc


def _fetch_uid(client: imaplib.IMAP4, uid: str) -> InboxMessage:
    status, data = client.uid("FETCH", uid, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        raise VacationImapError(f"Не удалось прочитать письмо UID {uid}")
    raw = data[0]
    payload = raw[1] if isinstance(raw, tuple) and len(raw) > 1 else b""
    if not isinstance(payload, (bytes, bytearray)):
        raise VacationImapError(f"Пустое письмо UID {uid}")
    message = email.message_from_bytes(bytes(payload))
    subject = _decode_header(message.get("Subject"))
    message_id = (message.get("Message-ID") or message.get("Message-Id") or "").strip()
    body = _body_text(message)
    key_source = message_id or sha256(body.encode("utf-8", errors="replace")).hexdigest()
    return InboxMessage(
        message_key=key_source[:500],
        uid=uid,
        subject=subject[:500],
        body=body,
        received_at=_received_at(message),
    )


def _body_text(message: Message) -> str:
    plain = ""
    html = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if disposition.startswith("attachment"):
                continue
            kind = (part.get_content_type() or "").lower()
            text = _decode_part(part)
            if kind == "text/plain" and not plain:
                plain = text
            elif kind == "text/html" and not html:
                html = text
    else:
        kind = (message.get_content_type() or "").lower()
        text = _decode_part(message)
        if kind == "text/html":
            html = text
        else:
            plain = text
    if plain.strip():
        return plain
    return html_to_text(html)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    if not isinstance(payload, (bytes, bytearray)):
        return str(payload)
    charset = part.get_content_charset() or "utf-8"
    try:
        return bytes(payload).decode(charset, errors="replace")
    except LookupError:
        return bytes(payload).decode("utf-8", errors="replace")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    chunks: list[str] = []
    for text, encoding in decode_header(value):
        if isinstance(text, bytes):
            chunks.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            chunks.append(text)
    return "".join(chunks).strip()


def _received_at(message: Message) -> datetime | None:
    raw = message.get("Date")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _logout(client: imaplib.IMAP4 | None) -> None:
    if client is None:
        return
    try:
        client.logout()
    except (imaplib.IMAP4.error, OSError):
        shutdown = getattr(client, "shutdown", None)
        if shutdown is None:
            return
        try:
            shutdown()
        except (imaplib.IMAP4.error, OSError):
            return
