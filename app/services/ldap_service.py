"""Проверка пароля в LDAP и локальная копия пользователя.

Пароль в базу приложения не пишется: при каждом входе его заново
подтверждает каталог. Локальная строка users нужна Flask-Login
(сессия хранит id, а не пароль) и правам на секторы, которые
сравниваются с группами на момент последнего входа.

Пустой пароль отвергается до сети. Иначе сервер может принять
анонимный bind и пустить в систему без пароля.
"""

import logging
from dataclasses import dataclass

from flask import current_app
from ldap3 import NONE, SUBTREE, Connection, Server
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import select

from app.extensions import db
from app.models import User, UserLdapGroup
from app.utils import utcnow

logger = logging.getLogger(__name__)

# Дольше держать воркер на недоступном контроллере домена нельзя:
# страница входа обслуживается тем же процессом, что и остальной сайт.
_CONNECT_TIMEOUT_SECONDS = 5

_USERNAME_MAX = 128
_DISPLAY_NAME_MAX = 255
_EMAIL_MAX = 255
_DN_MAX = 512
_GROUP_NAME_MAX = 255


@dataclass
class LdapIdentity:
    username: str  # sAMAccountName в нижнем регистре
    display_name: str
    email: str | None
    dn: str
    groups: list[str]  # только CN, без полного DN
    is_admin: bool


def authenticate(username: str, password: str) -> LdapIdentity | None:
    """Вернуть личность из каталога или None.

    None означает и неверный пароль, и недоступный LDAP, и пустой пароль.
    Пароль в журнал не попадает: по тексту ошибки нельзя восстановить секрет.
    """
    if not isinstance(password, str) or password == "":
        return None

    typed = username.strip() if isinstance(username, str) else ""
    if not _username_allowed(typed):
        return None

    try:
        return _bind_and_search(typed, password)
    except Exception as exc:
        # Обрыв сети иногда приходит как OSError, а не как LDAPException.
        # Страница входа должна остаться формой с сообщением, а не ответом 500.
        service_password = str(current_app.config.get("LDAP_BIND_PASSWORD") or "")
        logger.warning(
            "Вход пользователя %s через LDAP не удался (%s): %s",
            typed,
            _endpoint_label(),
            _public_error(exc, (password, service_password)),
        )
        return None


def upsert_local_user(identity: LdapIdentity) -> User:
    """Создать или обновить пользователя и заменить его группы LDAP.

    Группы не дополняются, а пишутся заново: человек мог выйти из группы
    в каталоге, и старое членство не должно оставлять доступ к сектору.
    """
    username = identity.username.strip().lower()
    user = db.session.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, display_name=username)
        db.session.add(user)
        # id нужен внешнему ключу групп. flush ещё не закрывает транзакцию.
        db.session.flush()

    display_name = (identity.display_name or username).strip() or username
    user.display_name = display_name[:_DISPLAY_NAME_MAX]
    email = (identity.email or "").strip()
    user.email = email[:_EMAIL_MAX] if email else None
    dn = (identity.dn or "").strip()
    user.ldap_dn = dn[:_DN_MAX] if dn else None
    user.is_admin = bool(identity.is_admin)
    user.last_login_at = utcnow()

    user.ldap_groups.clear()
    # Уникальный ключ (user_id, group_name) проверяется сразу.
    # Пока старые строки не удалены, повторная вставка того же CN падает.
    db.session.flush()

    seen: set[str] = set()
    for group_name in identity.groups:
        cleaned = group_name.strip()[:_GROUP_NAME_MAX]
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        user.ldap_groups.append(UserLdapGroup(group_name=cleaned))

    db.session.commit()
    return user


def _username_allowed(username: str) -> bool:
    """Имя можно подставлять в фильтр только если это само имя, а не кусок запроса.

    «*» в LDAP-фильтре значит «любые символы», скобки меняют условие.
    Белый список отсекает такое до обращения к каталогу. Точка, подчёркивание
    и дефис остаются: они встречаются в sAMAccountName. Символ @ не принимаем
    из формы — домен приложение добавляет само из LDAP_DOMAIN.
    """
    if not username or len(username) > _USERNAME_MAX:
        return False
    return all(char.isalpha() or char.isdigit() or char in "._-" for char in username)


def _bind_and_search(username: str, password: str) -> LdapIdentity | None:
    cfg = current_app.config
    host = str(cfg.get("LDAP_HOST") or "").strip()
    if not host:
        logger.error(
            "LDAP_HOST пуст, проверка пароля невозможна. "
            "Укажите адрес контроллера домена в окружении."
        )
        return None

    port = int(cfg.get("LDAP_PORT") or 636)
    use_ssl = _as_bool(cfg.get("LDAP_USE_SSL", True), default=True)
    host, port, use_ssl = _ldap_endpoint(host, port, use_ssl)
    base_dn = str(cfg.get("LDAP_BASE_DN") or "")
    service_dn = str(cfg.get("LDAP_BIND_DN") or "").strip()
    service_password = str(cfg.get("LDAP_BIND_PASSWORD") or "")
    user_filter = str(cfg.get("LDAP_USER_FILTER") or "(sAMAccountName={username})")
    admin_group = str(cfg.get("LDAP_ADMIN_GROUP") or "")
    domain = str(cfg.get("LDAP_DOMAIN") or "").strip()

    # escape_filter_chars — вторая линия после белого списка: шаблон фильтра
    # приходит из настроек, и имя не должно стать частью его синтаксиса.
    search_filter = user_filter.format(username=escape_filter_chars(username))
    # user@domain — это UPN. Active Directory принимает его простым bind
    # без знания полного DN. Без домена каталог ждёт имя как есть.
    bind_user = f"{username}@{domain}" if domain else username

    # get_info=NONE: иначе ldap3 после bind сам читает схему каталога.
    # Сбой этого лишнего запроса выглядел бы как неверный пароль.
    server = Server(
        host,
        port=port,
        use_ssl=use_ssl,
        get_info=NONE,
        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
    )
    user_conn = Connection(
        server,
        user=bind_user,
        password=password,
        auto_bind=True,
        receive_timeout=_CONNECT_TIMEOUT_SECONDS,
    )
    service_conn = None
    try:
        if service_dn:
            # Пароль уже доказан. Поиск от сервисной учётки нужен потому,
            # что обычный пользователь часто не может прочитать memberOf.
            # Его соединение закрываем сразу: держать его открытым больше незачем.
            _unbind(user_conn)
            service_conn = Connection(
                server,
                user=service_dn,
                password=service_password,
                auto_bind=True,
                receive_timeout=_CONNECT_TIMEOUT_SECONDS,
            )
            reader = service_conn
        else:
            reader = user_conn

        reader.search(
            search_base=base_dn,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["displayName", "mail", "memberOf", "cn", "sAMAccountName"],
        )
        entries = list(reader.entries or [])
        if not entries:
            logger.warning(
                "Пароль пользователя %s принят, но фильтр не нашёл запись в каталоге",
                username,
            )
            return None
        # sAMAccountName в домене один. Первой записи достаточно.
        return _identity_from_entry(entries[0], username, admin_group)
    finally:
        if service_conn is not None:
            _unbind(service_conn)
        _unbind(user_conn)


def _identity_from_entry(entry: object, typed_username: str, admin_group: str) -> LdapIdentity | None:
    dn = getattr(entry, "entry_dn", None)
    if not isinstance(dn, str) or not dn.strip():
        logger.warning(
            "Запись LDAP пользователя %s без DN, локальную учётку не создаём",
            typed_username,
        )
        return None

    attrs = _attribute_map(entry)
    account = _first(attrs, "samaccountname") or typed_username
    username = account.strip().lower()
    if not username or len(username) > _USERNAME_MAX:
        username = typed_username.lower()

    display_name = _first(attrs, "displayname") or _first(attrs, "cn") or username
    groups = _group_names(attrs.get("memberof", []))
    admin_key = admin_group.strip().casefold()
    # Сравнение без регистра: в каталоге CN часто написан иначе, чем в LDAP_ADMIN_GROUP.
    is_admin = bool(admin_key) and any(group.casefold() == admin_key for group in groups)
    return LdapIdentity(
        username=username,
        display_name=display_name,
        email=_first(attrs, "mail"),
        dn=dn.strip(),
        groups=groups,
        is_admin=is_admin,
    )


def _group_names(member_of: list[str]) -> list[str]:
    """CN из DN групп.

    memberOf приходит как «CN=netops,OU=Groups,DC=example,DC=com».
    В user_ldap_groups и в правах на сектор лежит короткое имя:
    полный путь в каталоге при переносе OU пришлось бы переписывать.
    """
    groups: list[str] = []
    seen: set[str] = set()
    for group_dn in member_of:
        cn = _cn_from_dn(group_dn)
        if not cn or cn in seen:
            continue
        seen.add(cn)
        groups.append(cn)
    return groups


def _cn_from_dn(dn: str) -> str:
    """Текст после первого CN= до следующей запятой.

    Запятая отделяет CN от OU и DC. Экранированная запятая (\\,) — часть имени,
    а не конец компонента, поэтому по ней не режем.
    """
    current: list[str] = []
    parts: list[str] = []
    escaped = False
    for char in str(dn):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ",":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)

    for part in parts:
        if part[:3].upper() == "CN=":
            return part[3:].strip()
    return ""


def _attribute_map(entry: object) -> dict[str, list[str]]:
    raw = getattr(entry, "entry_attributes_as_dict", None)
    if not isinstance(raw, dict) or not raw:
        raw = {}
        for name in ("displayName", "mail", "memberOf", "cn", "sAMAccountName"):
            attr = getattr(entry, name, None)
            if attr is None:
                continue
            values = getattr(attr, "values", None)
            if values is None:
                single = getattr(attr, "value", None)
                if single is None:
                    values = []
                elif isinstance(single, (list, tuple)):
                    values = list(single)
                else:
                    values = [single]
            raw[name] = values

    normalized: dict[str, list[str]] = {}
    for key, values in raw.items():
        if isinstance(values, (str, bytes)):
            values = [values]
        texts: list[str] = []
        for item in values or []:
            if isinstance(item, bytes):
                item = item.decode("utf-8", errors="replace")
            text = str(item).strip()
            if text:
                texts.append(text)
        normalized[str(key).lower()] = texts
    return normalized


def _first(attrs: dict[str, list[str]], name: str) -> str | None:
    values = attrs.get(name.lower()) or []
    return values[0] if values else None


def _ldap_endpoint(host: str, port: int, use_ssl: bool) -> tuple[str, int | None, bool]:
    """Адрес, порт и SSL для ldap3.Server.

    AD Password Notifier хранит сервер одной строкой: ldap://хост — это
    порт 389 без шифрования, ldaps://хост — порт 636 с TLS. Если такую
    строку передать вместе с явным LDAP_PORT=636, ldap3 снимет префикс
    ldap:// (и выключит SSL), а порт 636 оставит. Контроллер домена на
    636 ждёт TLS и рвёт открытый текст: Connection reset by peer.

    Порт в аргументах Server для URL не передаём: его выбирает схема.
    Хвост «/» срезаем сами: ldap3 вызывает rstrip, но результат не сохраняет,
    и имя «хост/» не резолвится (invalid server address).
    """
    raw = host.strip().rstrip("/")
    lowered = raw.lower()
    if lowered.startswith("ldaps://"):
        return raw, None, True
    if lowered.startswith("ldap://"):
        return raw, None, False
    if port == 636 and not use_ssl:
        logger.warning(
            "LDAP_PORT=636 при LDAP_USE_SSL=false: порт 636 принимает только TLS. "
            "Подключение пойдёт с SSL. Для LDAP без шифрования, как ldap:// в "
            "AD Password Notifier, укажите LDAP_PORT=389."
        )
        return raw, port, True
    return raw, port, use_ssl


def _endpoint_label() -> str:
    """Куда реально уходит bind. Пароля здесь нет, строку можно писать в журнал."""
    cfg = current_app.config
    host = str(cfg.get("LDAP_HOST") or "").strip()
    try:
        port = int(cfg.get("LDAP_PORT") or 636)
    except (TypeError, ValueError):
        port = 636
    use_ssl = _as_bool(cfg.get("LDAP_USE_SSL", True), default=True)
    host, port, use_ssl = _ldap_endpoint(host, port, use_ssl)
    shown_port = "из URL" if port is None else str(port)
    return f"{host} port={shown_port} ssl={use_ssl}"


def _as_bool(value: object, default: bool) -> bool:
    """Строка «false» не должна стать True через обычный bool().

    Пароль уходит на порт каталога. Перепутанный LDAP_USE_SSL открыл бы
    его без шифрования, хотя в настройке написано «выключено».
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _public_error(exc: Exception, secrets: tuple[str, ...]) -> str:
    detail = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "***")
    return detail


def _unbind(conn: Connection) -> None:
    try:
        conn.unbind()
    except Exception:
        # Закрытие не должно отменять уже собранную личность
        # и не должно прятать исходную ошибку каталога.
        return
