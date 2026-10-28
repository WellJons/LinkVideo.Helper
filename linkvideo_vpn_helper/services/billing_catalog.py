from __future__ import annotations

"""Read-only parsing of observed CRM tariff, archive-depth and country lists.

These endpoints return reference catalogs, NOT proof that a tariff is
available to assign to a particular service. Never infer write permissions
or business rules from flags such as is_showed, is_start, count_cameras.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from linkvideo_vpn_helper.services.billing_gateway import (
    ALLOWED_COUNTRIES, BillingClient, BillingPermissionDenied,
    BillingValidationError, validate_client,
)


@dataclass(frozen=True)
class TariffCatalogEntry:
    id: str
    name: str
    country: str
    account_type: str
    price: Decimal
    description: str = ""
    is_monitor: bool = False
    is_start: int | None = None
    test_days: int | None = None
    id_tariff_vc: str | None = None
    count_cameras: int | None = None
    start_tariff_lv_monitor: int | None = None
    is_showed: int | None = None


@dataclass(frozen=True)
class ArchivePeriod:
    id: str
    name: str
    days: int


@dataclass(frozen=True)
class BillingCountry:
    id: str
    code: str
    name: str
    currency_code: str
    prefix_login: str


@dataclass(frozen=True)
class TariffReferenceCatalog:
    tariffs: tuple[TariffCatalogEntry, ...]
    archive_periods: tuple[ArchivePeriod, ...]
    country: BillingCountry


@dataclass(frozen=True)
class AllTariffsSnapshot:
    tariffs: tuple[TariffCatalogEntry, ...]
    countries: tuple[BillingCountry, ...]


def parse_all_tariffs_snapshot(
    tariffs_payload: Mapping[str, Any], countries_payload: Mapping[str, Any],
) -> AllTariffsSnapshot:
    """Reference only: /api/tariffs/list/0 plus country currency dictionary."""
    return AllTariffsSnapshot(
        tariffs=parse_tariff_catalog(tariffs_payload),
        countries=parse_countries(countries_payload),
    )


def _records(payload: Mapping[str, Any], label: str) -> list[dict]:
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        raise BillingValidationError(f"Биллинг не подтвердил получение справочника «{label}».")
    records = payload.get("response")
    if not isinstance(records, list) or len(records) > 5000:
        raise BillingValidationError(f"Некорректный ответ справочника «{label}».")
    if not all(isinstance(item, dict) for item in records):
        raise BillingValidationError(f"Некорректные записи справочника «{label}».")
    return records


def _text(row: dict, key: str, *, required: bool = False) -> str:
    value = row.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise BillingValidationError(f"Некорректное поле {key} справочника CRM.")
    result = value.strip()
    if required and not result:
        raise BillingValidationError(f"Отсутствует поле {key} справочника CRM.")
    return result


def _number(row: dict, key: str, *, required: bool = False) -> int | None:
    value = row.get(key)
    if value is None:
        if required:
            raise BillingValidationError(f"Отсутствует числовое поле {key}.")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BillingValidationError(f"Некорректное число в поле {key}.")
    rendered = str(value)
    if not rendered.isascii() or not rendered.isdecimal():
        raise BillingValidationError(f"Некорректное число в поле {key}.")
    return int(rendered)


def _identifier(row: dict, key: str) -> str:
    number = _number(row, key, required=True)
    if number is None or number <= 0:
        raise BillingValidationError(f"Некорректный ID в поле {key}.")
    return str(number)


def parse_tariff_catalog(payload: Mapping[str, Any]) -> tuple[TariffCatalogEntry, ...]:
    rows = _records(payload, "Тарифы")
    found = []
    identifiers = set()
    for row in rows:
        ident = _identifier(row, "id_tariff")
        if ident in identifiers:
            raise BillingValidationError("Биллинг вернул повторяющийся ID тарифа.")
        identifiers.add(ident)
        raw_price = row.get("full_ab_plata")
        if raw_price is None or isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str)):
            raise BillingValidationError("В каталоге тарифов указана некорректная стоимость.")
        try:
            price = Decimal(str(raw_price))
        except InvalidOperation as exc:
            raise BillingValidationError("В каталоге тарифов указана некорректная стоимость.") from exc
        if not price.is_finite() or price < 0:
            raise BillingValidationError("В каталоге тарифов указана некорректная стоимость.")
        monitor_flag = _number(row, "is_tariff_lv_monitor")
        if monitor_flag not in (None, 0, 1):
            raise BillingValidationError("Неизвестный формат признака Monitor.")
        vc_id = _number(row, "id_tariff_vc")
        found.append(TariffCatalogEntry(
            id=ident,
            name=_text(row, "tariff_name", required=True),
            country=_text(row, "country", required=True),
            account_type=_text(row, "account_type_name", required=True),
            price=price,
            description=_text(row, "description"),
            is_monitor=monitor_flag == 1,
            is_start=_number(row, "is_start"),
            test_days=_number(row, "test_days"),
            id_tariff_vc=str(vc_id) if vc_id is not None else None,
            count_cameras=_number(row, "count_cameras"),
            start_tariff_lv_monitor=_number(row, "start_tariff_lv_monitor"),
            is_showed=_number(row, "is_showed"),
        ))
    return tuple(found)


def parse_archive_periods(payload: Mapping[str, Any]) -> tuple[ArchivePeriod, ...]:
    rows = _records(payload, "Глубина архива")
    seen = set()
    periods = []
    for row in rows:
        ident = _identifier(row, "id_archive_period")
        if ident in seen:
            raise BillingValidationError("Повторяющийся ID глубины архива.")
        seen.add(ident)
        days = _number(row, "days", required=True)
        periods.append(ArchivePeriod(
            id=ident, name=_text(row, "archive_period_name", required=True),
            days=days,
        ))
    return tuple(periods)


def parse_countries(payload: Mapping[str, Any]) -> tuple[BillingCountry, ...]:
    rows = _records(payload, "Страны")
    result = []
    ids, names = set(), set()
    for row in rows:
        ident = _identifier(row, "id_country")
        name = _text(row, "name", required=True)
        if ident in ids or name in names:
            raise BillingValidationError("Повторяющаяся страна в справочнике.")
        ids.add(ident)
        names.add(name)
        result.append(BillingCountry(
            id=ident,
            code=_text(row, "code", required=True),
            name=name,
            currency_code=_text(row, "currency_code", required=True),
            prefix_login=_text(row, "prefix_login", required=True),
        ))
    return tuple(result)


def filter_tariffs_for_client(
    tariffs: tuple[TariffCatalogEntry, ...], client: BillingClient,
) -> tuple[TariffCatalogEntry, ...]:
    validate_client(client)
    if not client.legal_type:
        raise BillingValidationError("Тип клиента не подтверждён. Каталог не может быть отфильтрован.")
    # CRM's real dropdown contains corporate/ЮЛ, Monitor and starter plans
    # even on individual client accounts. An exact legal-type equality check
    # incorrectly hid them. Country is a verified boundary; plan type is
    # metadata for display and the server decides actual eligibility on PUT.
    return tuple(tariff for tariff in tariffs if tariff.country == client.country)


def build_tariff_reference(
    client: BillingClient,
    tariff_payload: Mapping[str, Any],
    archive_payload: Mapping[str, Any],
    country_payload: Mapping[str, Any],
) -> TariffReferenceCatalog:
    validate_client(client)
    if client.country not in ALLOWED_COUNTRIES:
        raise BillingPermissionDenied("Страна клиента не поддерживается.")
    countries = parse_countries(country_payload)
    country = next((item for item in countries if item.name == client.country), None)
    if country is None:
        raise BillingValidationError("Страна клиента отсутствует в справочнике биллинга.")
    tariffs = filter_tariffs_for_client(parse_tariff_catalog(tariff_payload), client)
    return TariffReferenceCatalog(
        tariffs=tariffs,
        archive_periods=parse_archive_periods(archive_payload),
        country=country,
    )
