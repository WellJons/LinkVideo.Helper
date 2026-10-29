from __future__ import annotations

"""Parse the observed GET /api/accounts/{id} CRM response without network I/O.

The account response contains no camera list, billing currency or country.
These must not be guessed: the gateway supplies a trusted country from its
configured source, and another endpoint is required for the services.
"""

from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingService, BillingValidationError, validate_client,
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


def parse_cameras_response(payload: Mapping[str, Any]) -> tuple[BillingService, ...]:
    """Parse observed GET /api/accounts/{id}/cameras response.

    Observed state mappings: status='1' -> 'Активный',
    status='0' -> 'Неактивный'. The
    numeric price/discount/costs fields have no currency marker.
    """
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        raise BillingValidationError("Биллинг не подтвердил получение списка камер.")
    rows = payload.get("response")
    if not isinstance(rows, list) or len(rows) > 2000:
        raise BillingValidationError("Биллинг вернул некорректный список камер.")

    services: list[BillingService] = []
    seen: set[str] = set()
    for record in rows:
        if not isinstance(record, dict):
            raise BillingValidationError("В списке камер обнаружена некорректная запись.")
        camera_id = _value(record, "id_camera")
        if not camera_id.isascii() or not camera_id.isdecimal() or int(camera_id) <= 0:
            raise BillingValidationError("Камера не имеет корректного ID в биллинге.")
        if camera_id in seen:
            raise BillingValidationError("Биллинг вернул повторяющийся ID камеры.")
        seen.add(camera_id)

        vc_id = _value(record, "id_camera_vc")
        if vc_id and (not vc_id.isascii() or not vc_id.isdecimal()):
            raise BillingValidationError("Биллинг вернул некорректный id_camera_vc.")
        status_code = _value(record, "status")
        if status_code == "1":
            status = "Активный"
        elif status_code == "0":
            status = "Неактивный"
        elif status_code:
            status = f"Код статуса: {status_code}"
        else:
            status = "Неизвестно"

        services.append(BillingService(
            id=camera_id,
            camera_id=camera_id,
            camera_vc_id=vc_id,
            camera_name=_value(record, "short_name"),
            tariff_name=_value(record, "tariff_name"),
            price_text=_value(record, "ab_plata"),
            # No percent/currency suffix: the API did not specify units.
            discount_text=_value(record, "discount"),
            total_text=_value(record, "costs"),
            status=status,
        ))
    return tuple(services)


def attach_cameras_to_client(client: BillingClient, payload: Mapping[str, Any]) -> BillingClient:
    """Attach cameras fetched for this same verified account ID.

    The caller must bind GET /api/accounts/{client.id}/cameras to the current
    account; the response itself contains no account ID.
    """
    validate_client(client)
    return validate_client(replace(
        client, services=parse_cameras_response(payload), services_loaded=True
    ))
