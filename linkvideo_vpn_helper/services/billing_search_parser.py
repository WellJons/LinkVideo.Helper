from __future__ import annotations

"""Verified shape of GET /api/accounts/search/{query}; no network access."""

from dataclasses import dataclass
from typing import Any, Mapping

from linkvideo_vpn_helper.services.billing_gateway import (
    ALLOWED_COUNTRIES, BillingPermissionDenied, BillingValidationError,
    validate_login,
)


def _field(record: Mapping[str, Any], name: str) -> str:
    value = record.get(name)
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise BillingValidationError("Биллинг вернул некорректное поле поиска клиента.")
    return str(value).strip()


@dataclass(frozen=True)
class BillingSearchHit:
    id: str
    login: str
    account_name: str
    country: str
    account_type: str
    status: str
    services_status: str
    balance_text: str


def parse_search_response(payload: Mapping[str, Any]) -> tuple[BillingSearchHit, ...]:
    """Safely parse any number of search matches.

    Partial matches must not silently select the wrong client. Only the
    separate select_exact_search_hit function may choose an account for
    viewing or tariff changes.
    """
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        raise BillingValidationError("Биллинг не подтвердил результат поиска.")
    rows = payload.get("response")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise BillingValidationError("Биллинг вернул некорректный список результатов поиска.")

    found = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise BillingValidationError("Биллинг вернул некорректного клиента в поиске.")
        identifier = _field(row, "id_account")
        if not identifier.isascii() or not identifier.isdecimal() or int(identifier) <= 0:
            raise BillingValidationError("Биллинг вернул некорректный ID аккаунта.")
        if identifier in seen:
            raise BillingValidationError("Биллинг вернул дублирующиеся ID аккаунтов.")
        seen.add(identifier)
        login = _field(row, "login")
        if not login:
            raise BillingValidationError("В результате поиска отсутствует логин.")
        found.append(BillingSearchHit(
            id=identifier,
            login=login,
            account_name=_field(row, "account_name"),
            country=_field(row, "country_name"),
            account_type=_field(row, "account_type_name"),
            status=_field(row, "status_name"),
            services_status=_field(row, "services_status"),
            balance_text=_field(row, "balance"),
        ))
    return tuple(found)


def select_exact_search_hit(
    hits: tuple[BillingSearchHit, ...], query: str
) -> BillingSearchHit | None:
    """Accept only one exact login or account-name match, never a fuzzy one."""
    requested = validate_login(query).casefold()
    exact = [
        hit for hit in hits
        if requested in (hit.login.casefold(), hit.account_name.casefold())
    ]
    if not exact:
        return None
    if len(exact) != 1:
        raise BillingValidationError(
            "Найдено несколько клиентов с одинаковым логином. Уточните запрос."
        )
    selected = exact[0]
    if selected.country not in ALLOWED_COUNTRIES:
        raise BillingPermissionDenied(
            "Пока поддерживаются только клиенты Беларуси и Казахстана."
        )
    return selected
