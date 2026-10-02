"""CRUD и проверка доступности сервисов на экране входа.

Маршруты только читают форму / отдают JSON.
ICMP идёт через ping_service после resolve_to_ipv4.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import DeviceStatus, LoginService
from app.services.net_utils import NetworkInputError, assert_host_or_ipv4, resolve_to_ipv4
from app.services.ping_service import ping_host

_NAME_MAX = 128
_ADDRESS_MAX = 255
_MAX_WORKERS = 8
_PING_TIMEOUT_S = 1


class LoginServiceError(ValueError):
    """Ошибка данных сервиса. Текст уже можно показать в flash."""


@dataclass(frozen=True)
class ServiceStatus:
    id: int
    name: str
    address: str
    online: bool
    latency_ms: int | None


def list_services(*, enabled_only: bool = False) -> list[LoginService]:
    stmt = select(LoginService).order_by(LoginService.sort_order, LoginService.id)
    if enabled_only:
        stmt = stmt.where(LoginService.is_enabled.is_(True))
    return list(db.session.scalars(stmt).all())


def save_service(
    service_id: int | None,
    name: str,
    address: str,
    sort_order: int | str | None = 0,
    is_enabled: bool = True,
) -> LoginService:
    clean_name = _clean_name(name)
    clean_address = assert_host_or_ipv4(address)
    if len(clean_address) > _ADDRESS_MAX:
        raise LoginServiceError(f"Адрес длиннее {_ADDRESS_MAX} символов.")
    order = _clean_sort_order(sort_order)
    _ensure_unique_name(clean_name, service_id)

    try:
        service = _get_or_create(service_id)
        service.name = clean_name
        service.address = clean_address
        service.sort_order = order
        service.is_enabled = bool(is_enabled)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise LoginServiceError("Сервис с таким названием уже есть.") from exc
    except Exception:
        db.session.rollback()
        raise
    return service


def delete_service(service_id: int) -> None:
    service = db.session.get(LoginService, service_id)
    if service is None:
        raise LoginServiceError("Сервис не найден.")
    db.session.delete(service)
    db.session.commit()


def check_enabled_services() -> list[ServiceStatus]:
    """Параллельный ICMP по включённым сервисам. Для публичного API входа."""
    services = list_services(enabled_only=True)
    if not services:
        return []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(services))) as pool:
        return list(pool.map(_check_one, services))


def _check_one(service: LoginService) -> ServiceStatus:
    try:
        ip = resolve_to_ipv4(service.address)
        result = ping_host(ip, timeout_s=_PING_TIMEOUT_S)
    except (NetworkInputError, OSError):
        return ServiceStatus(
            id=service.id,
            name=service.name,
            address=service.address,
            online=False,
            latency_ms=None,
        )
    online = result.status == DeviceStatus.ONLINE
    return ServiceStatus(
        id=service.id,
        name=service.name,
        address=service.address,
        online=online,
        latency_ms=result.response_time_ms if online else None,
    )


def _get_or_create(service_id: int | None) -> LoginService:
    if service_id is None:
        service = LoginService(
            name="",
            address="",
            sort_order=0,
            is_enabled=True,
        )
        db.session.add(service)
        return service
    service = db.session.get(LoginService, service_id)
    if service is None:
        raise LoginServiceError("Сервис не найден.")
    return service


def _clean_name(name: str) -> str:
    clean = (name or "").strip()
    if not clean:
        raise LoginServiceError("Укажите название сервиса.")
    if len(clean) > _NAME_MAX:
        raise LoginServiceError(f"Название длиннее {_NAME_MAX} символов.")
    return clean


def _clean_sort_order(value: int | str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        order = int(value)
    except (TypeError, ValueError) as exc:
        raise LoginServiceError("Порядок должен быть целым числом.") from exc
    return order


def _ensure_unique_name(name: str, service_id: int | None) -> None:
    existing = db.session.scalar(
        select(LoginService).where(LoginService.name == name)
    )
    if existing is not None and existing.id != service_id:
        raise LoginServiceError("Сервис с таким названием уже есть.")
