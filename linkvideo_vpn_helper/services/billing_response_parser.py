from __future__ import annotations

"""Parse the observed GET /api/accounts/{id} CRM response without network I/O.

The account response contains no camera list, billing currency or country.
These must not be guessed: the gateway supplies a trusted country from its
configured source, and another endpoint is required for the services.
"""

from datetime import datetime
from typing import Any, Mapping

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingValidationError, validate_client,
)


def _value(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value).strip()
    raise BillingValidationError("Биллинг вернул некорректный формат карточки клиента.")


def _date_text(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError as exc:
        raise BillingValidationError("Биллинг вернул некорректную дату регистрации.") from exc


def parse_account_detail(
    payload: Mapping[str, Any], *, account_id: str | int,
    country: str, expected_login: str = "",
) -> BillingClient | None:
    """Map verified account JSON to the application model.

    Never accept a different login than the one that was requested. Reject
    multiple matches: choosing the first one could edit another customer's
    subscription later.
    """
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        raise BillingValidationError("Биллинг не подтвердил получение данных клиента.")
    identifier = str(account_id).strip()
    if not identifier.isascii() or not identifier.isdecimal() or int(identifier) <= 0:
        raise BillingValidationError("Недопустимый идентификатор клиента.")
    records = payload.get("response")
    if not isinstance(records, list):
        raise BillingValidationError("Биллинг вернул неожиданный формат списка клиентов.")
    if not records:
        return None
    if len(records) != 1 or not isinstance(records[0], dict):
        raise BillingValidationError("Биллинг вернул неоднозначный ответ о клиенте.")

    row = records[0]
    login = _value(row, "login")
    if not login:
        raise BillingValidationError("В карточке биллинга отсутствует логин.")
    if expected_login.strip() and login.casefold() != expected_login.strip().casefold():
        raise BillingValidationError("Ответ биллинга относится к другому клиенту.")

    client = BillingClient(
        id=identifier,
        login=login,
        display_name=_value(row, "account_name"),
        country=country,
        status=_value(row, "status_name"),
        contract_number=_value(row, "dog_num"),
        # The response does not declare a currency: do not invent ₽/₸/Br.
        balance_text=_value(row, "balance"),
        email=_value(row, "email"),
        phone=_value(row, "phone_number"),
        manager=_value(row, "manager_name"),
        registered_at=_date_text(_value(row, "registration_date")),
        address=_value(row, "address"),
        services=(),
        services_loaded=False,
    )
    return validate_client(client)
