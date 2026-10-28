from __future__ import annotations

"""Typed contract for a future, supported corporate billing API.

The real endpoints and authentication flow are not yet confirmed. No live
request is attempted until a backend adapter implementing this protocol is
configured. All write operations must also be authorized server-side.
"""

from dataclasses import dataclass
from datetime import date
import re
from typing import Protocol


ALLOWED_COUNTRIES = frozenset({"Беларусь", "Казахстан"})
_LOGIN_RE = re.compile(r"^[A-Za-z0-9_.+@-]{3,100}$")


class BillingNotConfigured(RuntimeError):
    pass


class BillingPermissionDenied(PermissionError):
    pass


class BillingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class BillingService:
    id: str
    camera_name: str
    camera_id: str
    tariff_name: str
    price_text: str
    discount_text: str
    total_text: str
    status: str
    # Secondary ID included in the CRM response. Keep it separate from the
    # camera/service ID until the tariff-change API documents its meaning.
    camera_vc_id: str = ""


@dataclass(frozen=True)
class BillingClient:
    id: str
    login: str
    country: str
    status: str
    # Human-facing billing account name. Distinct from the login.
    display_name: str = ""
    legal_type: str = ""
    contract_number: str = ""
    balance_text: str = ""
    email: str = ""
    phone: str = ""
    manager: str = ""
    address: str = ""
    registered_at: str = ""
    services: tuple[BillingService, ...] = ()
    # GET /api/accounts/{id} has no camera list. Do not display 0 services
    # until GET /api/accounts/{id}/cameras has actually been parsed.
    services_loaded: bool = False


@dataclass(frozen=True)
class TariffOption:
    id: str
    name: str


@dataclass(frozen=True)
class TariffChange:
    client_id: str
    service_id: str
    tariff_id: str
    effective_date: date
    comment: str
    # Opaque server version/etag to prevent stale changes when available.
    expected_revision: str = ""


class BillingGateway(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def can_change_tariffs(self) -> bool: ...

    def find_client(self, login: str) -> BillingClient | None: ...

    def tariff_options(self, client_id: str, service_id: str) -> tuple[TariffOption, ...]: ...

    def change_tariff(self, change: TariffChange) -> BillingClient: ...


def validate_login(login: str) -> str:
    normalized = (login or "").strip()
    if not _LOGIN_RE.fullmatch(normalized):
        raise BillingValidationError("Введите корректный логин клиента без пробелов.")
    return normalized


def validate_client(client: BillingClient) -> BillingClient:
    if client.country not in ALLOWED_COUNTRIES:
        raise BillingPermissionDenied(
            "Сейчас поддерживаются только клиенты Беларуси и Казахстана."
        )
    if not client.id or not client.login:
        raise BillingValidationError("Биллинг вернул неполную карточку клиента.")
    if any(not row.id for row in client.services):
        raise BillingValidationError("Биллинг вернул услугу без идентификатора.")
    return client


def validate_tariff_change(
    client: BillingClient, service: BillingService, tariff: TariffOption,
    effective_date: date, comment: str,
) -> TariffChange:
    validate_client(client)
    if service not in client.services:
        raise BillingValidationError("Выбранная услуга не принадлежит клиенту.")
    if not tariff.id:
        raise BillingValidationError("Не указан тариф.")
    if effective_date < date.today():
        raise BillingValidationError("Дата изменения тарифа не может быть в прошлом.")
    if len(comment) > 500:
        raise BillingValidationError("Комментарий не должен превышать 500 символов.")
    return TariffChange(client.id, service.id, tariff.id, effective_date, comment.strip())


class UnconfiguredBillingGateway:
    """Production-safe default, does not guess CRM endpoints or reuse cookies."""

    configured = False
    can_change_tariffs = False

    def find_client(self, login: str) -> BillingClient | None:
        raise BillingNotConfigured(
            "API нового биллинга ещё не подключён. "
            "Для поиска клиента требуется согласованный серверный интерфейс."
        )

    def tariff_options(self, client_id: str, service_id: str) -> tuple[TariffOption, ...]:
        raise BillingNotConfigured("Список тарифов пока недоступен: API не подключён.")

    def change_tariff(self, change: TariffChange) -> BillingClient:
        raise BillingNotConfigured("Изменение тарифа недоступно: API не подключён.")
