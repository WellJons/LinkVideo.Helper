from __future__ import annotations

"""Authenticated tariff updates through the observed CRM PUT endpoint only.

Catalog entries are filtered by exact CRM country and legal type. IDs are
taken from the actual country-specific catalogue, never from camera_vc_id.
A scheduled update may not be visible on the camera's GET until effective day.
"""

import re
from dataclasses import dataclass
from typing import Callable

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingPermissionDenied, BillingValidationError, TariffChange,
)
from linkvideo_vpn_helper.services.billing_readonly_gateway import BillingReadOnlyGateway
from linkvideo_vpn_helper.services.billing_catalog import filter_tariffs_for_client
from linkvideo_vpn_helper.services.billing_token_transport import (
    BillingTokenTransport, BillingWriteOutcomeUnknown,
)


@dataclass(frozen=True)
class TariffChangeReceipt:
    camera_id: str
    tariff_id: str
    message: str
    remain_pay: str
    updated_client: BillingClient | None = None


def validated_tariff_receipt(data: dict, change: TariffChange) -> TariffChangeReceipt:
    if data.get("success") is not True:
        # A server "success":false is a definite rejection, not permission to
        # treat the attempted write as already confirmed.
        raise BillingValidationError("Биллинг не подтвердил изменение тарифа.")
    response = data.get("response")
    if not isinstance(response, dict):
        raise BillingWriteOutcomeUnknown(
            "Ответ смены тарифа не содержит подтверждения. Проверьте биллинг."
        )
    message = response.get("message")
    result = response.get("result")
    if not isinstance(message, str) or not message.strip() or not isinstance(result, (str, int)):
        raise BillingWriteOutcomeUnknown(
            "Нет подтверждения результата PUT. Проверьте биллинг перед повтором."
        )
    pay = response.get("remain_pay")
    if isinstance(pay, bool) or (pay is not None and not isinstance(pay, (str, int, float))):
        pay = None
    return TariffChangeReceipt(
        camera_id=change.service_id,
        tariff_id=change.tariff_id,
        message=message.strip()[:400],
        remain_pay="" if pay is None else str(pay),
    )


class BillingEditableGateway(BillingReadOnlyGateway):
    configured = True
    can_change_tariffs = True

    def __init__(self, transport: BillingTokenTransport):
        super().__init__(transport.get_json)
        self._transport = transport

    def change_tariff(self, change: TariffChange, *, client: BillingClient) -> TariffChangeReceipt:
        """Explicit user-confirmed write for one verified customer's camera."""
        if not isinstance(change, TariffChange):
            raise BillingValidationError("Не указан объект изменения тарифа.")
        if change.client_id != client.id:
            raise BillingPermissionDenied("Выбран другой клиент. Повторите поиск.")
        if not re.fullmatch(r"[1-9][0-9]{0,17}", change.service_id):
            raise BillingValidationError("У камеры нет проверенного id_camera.")
        if not re.fullmatch(r"[1-9][0-9]{0,17}", change.tariff_id):
            raise BillingValidationError("У тарифа нет проверенного id_tariff.")
        if not any(s.id == change.service_id and s.camera_id == change.service_id
                   for s in client.services):
            raise BillingPermissionDenied("Эта камера не принадлежит выбранному клиенту.")
        catalog = self.reference_catalog(client)
        allowed = filter_tariffs_for_client(catalog.tariffs, client)
        if not any(t.id == change.tariff_id for t in allowed):
            raise BillingPermissionDenied(
                "Тариф отсутствует в каталоге страны и типа клиента."
            )
        response = self._transport.put_tariff(
            change.service_id, change.tariff_id, change.effective_date
        )
        receipt = validated_tariff_receipt(response, change)
        # A GET following a successful scheduled PUT may still return the old
        # plan: never claim it was applied immediately. Refresh is best effort.
        try:
            fresh = self.find_client(client.login)
        except Exception:
            fresh = None
        return TariffChangeReceipt(
            camera_id=receipt.camera_id, tariff_id=receipt.tariff_id,
            message=receipt.message, remain_pay=receipt.remain_pay,
            updated_client=fresh if fresh is not None and fresh.id == client.id else None,
        )
