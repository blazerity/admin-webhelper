"""Заявка bAWH в bAD и ключ только на чтение одной карточки.

bAWH сам создаёт заявку. Подтверждение — в панели bAD
(Параметры → Интеграции), кнопки «Подтвердить» здесь нет.
poll_secret нужен, пока заявка не подтверждена. token (bad_in_…)
приходит один раз; дальше ходим только с ним.
BAD_ADMIN_TOKEN не используем: этим ключом нельзя получить список
устройств и нельзя ничего менять.

Секреты лежат в app_settings как Fernet, как пароль WMI.
На страницу и в логи они не попадают.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from app.extensions import db
from app.services.crypto_service import CryptoError, CryptoNotConfigured, decrypt, encrypt
from app.utils import as_truthy

logger = logging.getLogger(__name__)

INTEGRATION_NAME = "admin web helper"

STATUS_DISABLED = "disabled"
STATUS_PENDING = "pending"
STATUS_CONNECTED = "connected"
STATUS_REJECTED = "rejected"
STATUS_REVOKED = "revoked"

STATUS_LABELS = {
    STATUS_DISABLED: "Выключено",
    STATUS_PENDING: "Ждём подтверждения в bAD",
    STATUS_CONNECTED: "Подключено",
    STATUS_REJECTED: "Отклонено",
    STATUS_REVOKED: "Отозвано",
}

_ENABLED_KEY = "bad_integration_enabled"
_BASE_URL_KEY = "bad_integration_base_url"
_REQUEST_ID_KEY = "bad_integration_request_id"
_STATUS_KEY = "bad_integration_status"
_POLL_SECRET_KEY = "bad_integration_poll_secret_encrypted"
_TOKEN_KEY = "bad_integration_token_encrypted"
_NOTICE_KEY = "bad_integration_notice"

_REQUEST_ID_RE = re.compile(r"^int_[0-9a-f]{16}$")
_BAD_ID_RE = re.compile(r"^BAD-[0-9A-HJKMNP-TV-Z]{6}(?:[0-9A-HJKMNP-TV-Z]{4})?$")
_POLL_SECRET_RE = re.compile(r"^bad_ps_[A-Za-z0-9_\-]{8,}$")
_TOKEN_RE = re.compile(r"^bad_in_[A-Za-z0-9_\-]{8,}$")

_HTTP_TIMEOUT_S = 15
_MAX_BODY = 1_000_000

# После 401 в этом процессе новые lookup не стартуют, даже если
# параллельный поток уже прочитал ключ. Сбрасывается новой заявкой.
_stop_api = threading.Event()
_state_lock = threading.Lock()


class BadIntegrationError(RuntimeError):
    """Настройки или ответ bAD не дают продолжать."""


@dataclass(frozen=True)
class BadIntegrationView:
    """То, что можно показать на странице. Секретов здесь нет."""

    enabled: bool
    base_url: str
    status: str
    status_label: str
    request_id: str
    notice: str


@dataclass(frozen=True)
class BadHttpResult:
    status: int
    data: dict | None
    error: str


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    """Не ходить по редиректу: вместе с ним уехал бы Bearer."""

    def http_response(self, request, response):  # noqa: ARG002
        return response

    https_response = http_response


def is_bad_id(value: str | None) -> bool:
    """Код карточки bAD. Сами его не вычисляем — только узнаём формат."""
    return bool(_BAD_ID_RE.match((value or "").strip()))


def integration_view() -> BadIntegrationView:
    """Состояние для экрана настроек."""
    enabled = _enabled()
    stored = _raw(_STATUS_KEY) or ""
    if not enabled:
        status = STATUS_DISABLED
    elif stored in STATUS_LABELS and stored != STATUS_DISABLED:
        status = stored
    else:
        status = STATUS_DISABLED
    request_id = _raw(_REQUEST_ID_KEY) or ""
    if not _REQUEST_ID_RE.match(request_id):
        request_id = ""
    return BadIntegrationView(
        enabled=enabled,
        base_url=_raw(_BASE_URL_KEY) or "",
        status=status,
        status_label=STATUS_LABELS.get(status, STATUS_LABELS[STATUS_DISABLED]),
        request_id=request_id,
        notice=(_raw(_NOTICE_KEY) or "")[:300],
    )


def is_connected() -> bool:
    """Ключ есть и связь не выключена, не отклонена и не отозвана."""
    return connected_credentials() is not None


def connected_credentials() -> tuple[str, str] | None:
    """Адрес и ключ для рабочих потоков. None — lookup звать нельзя.

    Читать только из потока с app context. В пул опроса уходит уже пара
    строк, без сессии SQLAlchemy.
    """
    if _stop_api.is_set():
        return None
    if not _enabled():
        return None
    if (_raw(_STATUS_KEY) or "") != STATUS_CONNECTED:
        return None
    token = _read_token()
    base_url = (_raw(_BASE_URL_KEY) or "").strip()
    if token is None or not base_url:
        return None
    return base_url, token


def save_integration(*, base_url: str, enabled: bool) -> BadIntegrationView:
    """Записать адрес и тумблер. Включение без ключа создаёт заявку."""
    from app.services.settings_service import set_app_setting

    with _state_lock:
        if not enabled:
            set_app_setting(_ENABLED_KEY, "0")
            set_app_setting(_NOTICE_KEY, "")
            db.session.commit()
            return integration_view()

        url = normalize_base_url(base_url)
        previous_url = (_raw(_BASE_URL_KEY) or "").strip()
        if previous_url and previous_url != url:
            _erase_secrets(set_app_setting)
            set_app_setting(_REQUEST_ID_KEY, "")
            set_app_setting(_STATUS_KEY, "")
        set_app_setting(_ENABLED_KEY, "1")
        set_app_setting(_BASE_URL_KEY, url)
        set_app_setting(_NOTICE_KEY, "")

        status = _raw(_STATUS_KEY) or ""
        if status == STATUS_CONNECTED and _read_token() is not None and previous_url == url:
            _stop_api.clear()
            db.session.commit()
            return integration_view()

        try:
            if (
                status == STATUS_PENDING
                and _REQUEST_ID_RE.match(_raw(_REQUEST_ID_KEY) or "")
                and _read_poll_secret() is not None
                and previous_url == url
            ):
                db.session.commit()
            else:
                _open_request(set_app_setting, url)
                db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    poll_pending_request()
    return integration_view()


def poll_pending_request() -> BadIntegrationView:
    """Один GET заявки, пока нет ключа. Без заявки — ничего не делает."""
    from app.services.settings_service import set_app_setting

    with _state_lock:
        if not _enabled():
            return integration_view()
        if (_raw(_STATUS_KEY) or "") != STATUS_PENDING:
            return integration_view()
        request_id = _raw(_REQUEST_ID_KEY) or ""
        secret = _read_poll_secret()
        base_url = _raw(_BASE_URL_KEY) or ""
        if not _REQUEST_ID_RE.match(request_id) or secret is None or not base_url:
            return integration_view()

    result = api_request(
        "GET",
        f"/v1/integrations/requests/{request_id}",
        base_url=base_url,
        bearer=secret,
    )
    with _state_lock:
        # Пока ходили в сеть, админ мог выключить интеграцию или сменить заявку.
        if not _enabled() or (_raw(_STATUS_KEY) or "") != STATUS_PENDING:
            return integration_view()
        if (_raw(_REQUEST_ID_KEY) or "") != request_id:
            return integration_view()
        if result.status == 0:
            logger.info("bAD: заявка %s пока не ответила", request_id)
            return integration_view()
        if result.status == 401 or result.status == 404:
            _erase_secrets(set_app_setting)
            set_app_setting(_STATUS_KEY, STATUS_REJECTED)
            set_app_setting(
                _NOTICE_KEY,
                "Заявка в bAD не найдена или секрет больше не подходит. "
                "Сохраните настройки ещё раз — уйдёт новая заявка.",
            )
            db.session.commit()
            logger.info("bAD: заявка %s недоступна (%s)", request_id, result.status)
            return integration_view()
        if result.status != 200 or not isinstance(result.data, dict):
            logger.info("bAD: заявка %s, ответ %s", request_id, result.status)
            return integration_view()

        remote = str(result.data.get("status") or "").strip().lower()
        token = result.data.get("token")
        if remote in {STATUS_REJECTED, STATUS_REVOKED}:
            _forget_rejected(set_app_setting, remote)
            db.session.commit()
            logger.info("bAD: заявка %s — %s", request_id, remote)
            return integration_view()
        if isinstance(token, str) and token:
            _store_token(set_app_setting, token)
            db.session.commit()
            logger.info("bAD: ключ заявки %s получен", request_id)
            return integration_view()
        if remote == "active":
            # Ключ выдают один раз. Повторный опрос его уже не присылает.
            _erase_secrets(set_app_setting)
            set_app_setting(_STATUS_KEY, STATUS_REVOKED)
            set_app_setting(
                _NOTICE_KEY,
                "Сервер уже отдал ключ и повторно его не присылает. "
                "Связь не активна. Сохраните настройки ещё раз — уйдёт новая заявка.",
            )
            db.session.commit()
            logger.info("bAD: заявка %s уже без ключа", request_id)
            return integration_view()
        if remote not in {"pending", "approved", ""}:
            logger.info("bAD: заявка %s, статус не разобрали", request_id)
        return integration_view()


def lookup_device(device_id: str) -> BadHttpResult:
    """POST /v1/integrations/lookup из потока с app context."""
    pair = connected_credentials()
    if pair is None:
        return BadHttpResult(status=0, data=None, error="not_connected")
    return lookup_with_credentials(pair[0], pair[1], device_id)


def lookup_with_credentials(base_url: str, token: str, device_id: str) -> BadHttpResult:
    """Тот же lookup без обращения к БД. Можно звать из пула опроса.

    При 401 только ставим стоп. Стереть ключ должен основной поток
    (`mark_revoked`): сессия SQLAlchemy в рабочем потоке другая.
    """
    if _stop_api.is_set():
        return BadHttpResult(status=0, data=None, error="not_connected")
    guid = (device_id or "").strip()
    if not guid or len(guid) > 128 or any(ord(ch) < 32 for ch in guid):
        return BadHttpResult(status=0, data=None, error="bad_device_id")
    result = api_request(
        "POST",
        "/v1/integrations/lookup",
        base_url=base_url,
        bearer=token,
        json_body={"device_id": guid},
    )
    if result.status == 401:
        _stop_api.set()
        return BadHttpResult(status=401, data=None, error="revoked")
    return result


def mark_revoked() -> None:
    """Ключ больше не работает. Стираем его и перестаём ходить в API."""
    from app.services.settings_service import set_app_setting

    _stop_api.set()
    with _state_lock:
        if (_raw(_STATUS_KEY) or "") == STATUS_REVOKED and _read_token() is None:
            return
        _erase_secrets(set_app_setting)
        set_app_setting(_STATUS_KEY, STATUS_REVOKED)
        set_app_setting(
            _NOTICE_KEY,
            "Ключ отозван в bAD. Связь не активна. "
            "Сохраните настройки ещё раз, чтобы отправить новую заявку.",
        )
        if _enabled():
            db.session.commit()
        logger.info("bAD: ключ интеграции отозван, запросы остановлены")


def normalize_base_url(raw: str) -> str:
    """Корень сервера bAD: схема, хост, порт. Без логина и без пути."""
    text = (raw or "").strip()
    if not text:
        raise BadIntegrationError("Укажите адрес сервера bAD.")
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BadIntegrationError("Адрес bAD должен начинаться с http:// или https://.")
    if parsed.username or parsed.password:
        raise BadIntegrationError("В адресе bAD не указывайте логин и пароль.")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise BadIntegrationError("Адрес bAD — корень сервера, без пути и параметров.")
    return text.rstrip("/")


def api_request(
    method: str,
    path: str,
    *,
    base_url: str,
    bearer: str | None = None,
    json_body: dict | None = None,
    timeout: float = _HTTP_TIMEOUT_S,
) -> BadHttpResult:
    """Один запрос к bAD. Тело ответа в лог не пишем: там может быть ключ."""
    if not path.startswith("/v1/"):
        raise BadIntegrationError("Неверный путь API bAD.")
    url = base_url.rstrip("/") + path
    payload = None
    headers = {"Accept": "application/json", "User-Agent": "bawh"}
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            raw = exc.read(_MAX_BODY)
        except Exception:
            raw = b""
    except Exception:
        logger.info("bAD: нет ответа на %s %s", method, path)
        return BadHttpResult(status=0, data=None, error="нет ответа от bAD")

    if status in {301, 302, 303, 307, 308}:
        logger.info("bAD: %s %s вернул перенаправление, запрос остановлен", method, path)
        return BadHttpResult(status=status, data=None, error="redirect")

    data = _json_object(raw)
    if status >= 400:
        logger.info("bAD: %s %s → %s", method, path, status)
    return BadHttpResult(status=status, data=data, error="")


def _open_request(set_app_setting, base_url: str) -> None:
    _erase_secrets(set_app_setting)
    set_app_setting(_REQUEST_ID_KEY, "")
    result = api_request(
        "POST",
        "/v1/integrations/requests",
        base_url=base_url,
        json_body={"name": INTEGRATION_NAME},
    )
    if result.status == 429:
        set_app_setting(_STATUS_KEY, "")
        set_app_setting(
            _NOTICE_KEY,
            "bAD не принял заявку: слишком много открытых заявок. Повторите позже.",
        )
        raise BadIntegrationError(
            "bAD не принял заявку: слишком много открытых заявок."
        )
    if result.status != 200 or not isinstance(result.data, dict):
        set_app_setting(_STATUS_KEY, "")
        set_app_setting(_NOTICE_KEY, "Не удалось отправить заявку в bAD.")
        raise BadIntegrationError(
            f"Не удалось отправить заявку в bAD (ответ {result.status or 'нет'})."
        )
    request_id = str(result.data.get("id") or "").strip()
    secret = result.data.get("poll_secret")
    remote = str(result.data.get("status") or "").strip().lower()
    if remote != STATUS_PENDING or not _REQUEST_ID_RE.match(request_id):
        raise BadIntegrationError("bAD принял заявку в неожиданном виде.")
    if not isinstance(secret, str) or not _POLL_SECRET_RE.match(secret):
        raise BadIntegrationError("bAD не прислал секрет заявки.")
    try:
        set_app_setting(_POLL_SECRET_KEY, encrypt(secret))
    except (CryptoNotConfigured, CryptoError) as exc:
        raise BadIntegrationError(str(exc)) from exc
    set_app_setting(_TOKEN_KEY, "")
    set_app_setting(_REQUEST_ID_KEY, request_id)
    set_app_setting(_STATUS_KEY, STATUS_PENDING)
    set_app_setting(_NOTICE_KEY, "")
    _stop_api.clear()
    logger.info("bAD: создана заявка %s", request_id)


def _store_token(set_app_setting, token: str) -> None:
    if not _TOKEN_RE.match(token):
        logger.info("bAD: ключ заявки неожиданного вида, не сохраняем")
        raise BadIntegrationError("bAD прислал ключ неожиданного вида.")
    try:
        stored = encrypt(token)
    except (CryptoNotConfigured, CryptoError) as exc:
        raise BadIntegrationError(str(exc)) from exc
    set_app_setting(_TOKEN_KEY, stored)
    set_app_setting(_POLL_SECRET_KEY, "")
    set_app_setting(_STATUS_KEY, STATUS_CONNECTED)
    set_app_setting(_NOTICE_KEY, "")
    _stop_api.clear()


def _forget_rejected(set_app_setting, remote: str) -> None:
    _erase_secrets(set_app_setting)
    status = STATUS_REVOKED if remote == STATUS_REVOKED else STATUS_REJECTED
    set_app_setting(_STATUS_KEY, status)
    if status == STATUS_REVOKED:
        notice = "Ключ отозван в bAD. Связь не активна."
    else:
        notice = "Заявку отклонили в bAD. Связь не активна."
    set_app_setting(
        _NOTICE_KEY,
        notice + " Сохраните настройки ещё раз, чтобы отправить новую заявку.",
    )
    _stop_api.set()


def _erase_secrets(set_app_setting) -> None:
    set_app_setting(_POLL_SECRET_KEY, "")
    set_app_setting(_TOKEN_KEY, "")


def _enabled() -> bool:
    return as_truthy(_raw(_ENABLED_KEY), default=False)


def _raw(key: str) -> str | None:
    from app.services.settings_service import get_app_setting

    value = get_app_setting(key)
    if value is None:
        return None
    return str(value)


def _read_secret(key: str, pattern: re.Pattern[str]) -> str | None:
    blob = (_raw(key) or "").strip()
    if not blob:
        return None
    try:
        plain = decrypt(blob)
    except (CryptoNotConfigured, CryptoError):
        logger.info("bAD: не удалось прочитать зашифрованный ключ интеграции")
        return None
    if not pattern.match(plain):
        return None
    return plain


def _read_token() -> str | None:
    return _read_secret(_TOKEN_KEY, _TOKEN_RE)


def _read_poll_secret() -> str | None:
    return _read_secret(_POLL_SECRET_KEY, _POLL_SECRET_RE)


def _json_object(raw: bytes) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None
