from __future__ import annotations

"""Read-only CRM workflow. Network/authentication are intentionally injected.

No production transport is provided here because the CRM token/session model
and supported integration policy have not been established. The caller must
supply an authorized, approved JSON GET implementation; the default Helper
continues to use UnconfiguredBillingGateway.
"""

from dataclasses import replace
from typing import Any, Callable, Mapping
from urllib.parse import quote

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingNotConfigured, BillingValidationError,
    TariffChange, TariffOption, validate_client, validate_login,
)
from linkvideo_vpn_helper.services.billing_response_parser import (
    attach_cameras_to_client, parse_account_detail,
)
from linkvideo_vpn_helper.services.billing_search_parser import (
    BillingSearchHit, exact_search_hits, parse_search_response, select_exact_search_hit,
)
from linkvideo_vpn_helper.services.billing_catalog import (
    AllTariffsSnapshot, TariffReferenceCatalog, build_tariff_reference, parse_all_tariffs_snapshot,
)


JsonGet = Callable[[str], Mapping[str, Any]]


class BillingReadOnlyGateway:
    """Compose the three observed GET routes without any CRM write method."""

    configured = True
    can_change_tariffs = False

    def __init__(self, get_json: JsonGet):
        if not callable(get_json):
            raise TypeError("An approved authenticated JSON transport is required.")
        self._get_json = get_json

    def search_clients(self, query: str) -> tuple[BillingSearchHit, ...]:
        """Return exact accounts, even when multiple countries use one login."""
        name = validate_login(query)
        encoded = quote(name, safe="")
        rows = parse_search_response(self._get_json(f"/api/accounts/search/{encoded}"))
        return exact_search_hits(rows, name)

    def find_client_or_choices(self, query: str):
        """One result is loaded; multiple results require explicit user choice."""
        hits = self.search_clients(query)
        if len(hits) > 1:
            return hits
        return self.find_client_by_hit(hits[0]) if hits else None

    def find_client(self, login: str) -> BillingClient | None:
        """Compatibility helper: refuse ambiguous login rather than guessing."""
        hits = self.search_clients(login)
        if len(hits) > 1:
            raise BillingValidationError(
                "Найдено несколько клиентов с одинаковым логином. Выберите страну и ID."
            )
        return self.find_client_by_hit(hits[0]) if hits else None

    def find_client_by_hit(self, hit: BillingSearchHit) -> BillingClient:
        """Fetch account by stable CRM ID from the exact search response."""
        if not isinstance(hit, BillingSearchHit) or hit.country not in ("Беларусь", "Казахстан"):
            raise BillingValidationError("Выбранная запись клиента недействительна.")
        if not hit.id.isascii() or not hit.id.isdecimal() or int(hit.id) <= 0:
            raise BillingValidationError("Недопустимый ID клиента.")
        detail = parse_account_detail(
            self._get_json(f"/api/accounts/{hit.id}"),
            account_id=hit.id,
            country=hit.country,
            expected_login=hit.login,
        )
        if detail is None:
            raise BillingValidationError("Выбранный клиент больше не доступен.")
        detail = validate_client(replace(
            detail,
            legal_type=hit.account_type,
            services_status=hit.services_status,
            status=detail.status or hit.status,
            display_name=detail.display_name or hit.account_name,
            balance_text=detail.balance_text if detail.balance_text else hit.balance_text,
        ))
        return attach_cameras_to_client(
            detail, self._get_json(f"/api/accounts/{hit.id}/cameras")
        )

    def reference_catalog(self, client: BillingClient) -> TariffReferenceCatalog:
        """Show reference data only. Does NOT assert a tariff is assignable."""
        validate_client(client)
        # The trailing /1 parameters are observed exactly as supplied from
        # Network, but their meaning is NOT inferred or reused as country IDs.
        tariffs_payload = self._get_json("/api/tariffs/list/1")
        archive_payload = self._get_json("/api/cameras/archive-depth/1")
        countries_payload = self._get_json("/api/countries/1")
        return build_tariff_reference(
            client, tariffs_payload, archive_payload, countries_payload
        )

    def all_tariffs(self) -> AllTariffsSnapshot:
        """Global read-only list from the /list/0 endpoint seen in CRM."""
        return parse_all_tariffs_snapshot(
            self._get_json("/api/tariffs/list/0"),
            self._get_json("/api/countries/1"),
        )

    def tariff_options(self, client_id: str, service_id: str) -> tuple[TariffOption, ...]:
        raise BillingNotConfigured("Метод получения доступных тарифов ещё не подтверждён.")

    def change_tariff(self, change: TariffChange) -> BillingClient:
        raise BillingNotConfigured("Метод изменения тарифов ещё не подтверждён.")
