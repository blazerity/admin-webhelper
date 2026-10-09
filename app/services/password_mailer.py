"""SMTP-отправка писем о сроке пароля.

Параметры — из настроек модуля (app_settings) с fallback на SMTP_* в .env.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from flask import current_app, render_template

from app.services.password_expiry_settings import SmtpSettings, get_smtp_settings

logger = logging.getLogger(__name__)


class PasswordMailerError(RuntimeError):
    """Ошибка SMTP."""


class PasswordMailer:
    def __init__(
        self,
        smtp: SmtpSettings | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self._smtp = smtp or get_smtp_settings()
        self.dry_run = dry_run

    def render_user_notification(
        self,
        *,
        full_name: str,
        username: str,
        days_left: int | None,
        expired: bool,
        instructions_url: str,
    ) -> str:
        days_overdue = 0 if days_left is None else max(0, -days_left)
        return render_template(
            "email/user_notification.html",
            full_name=full_name,
            username=username,
            days_left=0 if days_left is None else max(days_left, 0),
            days_overdue=days_overdue,
            expired=expired,
            instructions_url=instructions_url,
        )

    def render_admin_report(self, context: dict[str, object]) -> str:
        return render_template("email/admin_report.html", **context)

    def send_html(self, recipients: list[str], subject: str, html_body: str) -> None:
        if not recipients:
            raise PasswordMailerError("Список получателей пуст")

        if self.dry_run:
            logger.info("DRY-RUN: письмо не отправлено: %s -> %s", subject, ", ".join(recipients))
            return

        if not self._smtp.configured:
            raise PasswordMailerError(
                "SMTP не настроен: укажите хост и From в Настройки → Почта (SMTP) "
                "(или SMTP_HOST / SMTP_FROM в .env)"
            )

        message = MIMEMultipart("alternative")
        message["From"] = self._smtp.from_address
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            self._send(message, recipients)
        except (OSError, smtplib.SMTPException) as exc:
            raise PasswordMailerError(
                f"Ошибка SMTP ({self._smtp.host}:{self._smtp.port}): {exc}"
            ) from exc
        logger.info("Письмо отправлено: %s -> %s", subject, ", ".join(recipients))

    def send_alert(self, recipients: list[str], error_text: str) -> None:
        body = (
            "<p>Ошибка проверки срока паролей AD (модуль bAWH).</p>"
            f"<pre style='white-space:pre-wrap'>{error_text}</pre>"
        )
        self.send_html(recipients, "Ошибка проверки срока паролей AD", body)

    def _send(self, message: MIMEMultipart, recipients: list[str]) -> None:
        with smtplib.SMTP(self._smtp.host, self._smtp.port, timeout=30) as client:
            client.ehlo()
            if self._smtp.use_starttls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self._smtp.username:
                client.login(self._smtp.username, self._smtp.password or "")
            client.sendmail(self._smtp.from_address, recipients, message.as_string())


def test_smtp_connection(smtp: SmtpSettings | None = None) -> str:
    smtp = smtp or get_smtp_settings()
    if not smtp.configured:
        raise PasswordMailerError(
            "SMTP не настроен: укажите хост и From в Настройки → Почта (SMTP)"
        )
    try:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as client:
            client.ehlo()
            if smtp.use_starttls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if smtp.username:
                client.login(smtp.username, smtp.password or "")
        return f"SMTP OK: {smtp.host}:{smtp.port}"
    except (OSError, smtplib.SMTPException) as exc:
        raise PasswordMailerError(f"SMTP недоступен ({smtp.host}:{smtp.port}): {exc}") from exc


def email_templates_exist() -> bool:
    root = Path(current_app.root_path) / "templates" / "email"
    return (root / "user_notification.html").is_file() and (root / "admin_report.html").is_file()
