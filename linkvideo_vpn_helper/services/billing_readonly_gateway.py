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
    parse_search_response, select_exact_search_hit,
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

    def find_client(self, login: str) -> BillingClient | None:
        query = validate_login(login)
        # Encoded path *segment*: user input must never become another route.
        encoded = quote(query, safe="")
        hits = parse_search_response(self._get_json(f"/api/accounts/search/{encoded}"))
        match = select_exact_search_hit(hits, query)
        if match is None:
            return None

        detail = parse_account_detail(
            self._get_json(f"/api/accounts/{match.id}"),
            account_id=match.id,
            country=match.country,
            expected_login=match.login,
        )
        if detail is None:
            raise BillingValidationError(
                "Клиент найден в поиске, но его карточка недоступна."
            )
        # The search result provides properties absent from the detail JSON.
        detail = validate_client(replace(
            detail,
            legal_type=match.account_type,
            services_status=match.services_status,
            status=detail.status or match.status,
            display_name=detail.display_name or match.account_name,
            balance_text=detail.balance_text if detail.balance_text else match.balance_text,
        ))
        # Never claim services were loaded until this request succeeds.
        return attach_cameras_to_client(
            detail, self._get_json(f"/api/accounts/{match.id}/cameras")
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
