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
    BillingClient, BillingService, BillingPermissionDenied, BillingValidationError, TariffChange,
)
from linkvideo_vpn_helper.services.billing_readonly_gateway import BillingReadOnlyGateway
from linkvideo_vpn_helper.services.billing_catalog import filter_tariffs_for_client
from linkvideo_vpn_helper.services.billing_response_parser import attach_cameras_to_client
from linkvideo_vpn_helper.services.billing_search_parser import BillingSearchHit
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

    def camera_history(self, client: BillingClient, service_id: str) -> tuple[dict, ...]:
        """Read observed GET camera history only for a customer-owned camera."""
        self._owned_camera(client, service_id)
        data = self._get_json(f"/api/cameras/{service_id}/history")
        if not isinstance(data, dict) or data.get("success") is not True:
            raise BillingValidationError("CRM не подтвердила историю камеры.")
        rows = data.get("response")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise BillingValidationError("Некорректный ответ истории камеры.")
        output = []
        for row in rows:
            if not isinstance(row, dict):
                raise BillingValidationError("Некорректная строка истории.")
            output.append({
                key: str(row.get(key) or "")[:350]
                for key in ("date", "user", "action", "field_name",
                            "old_value", "new_value")
            })
        return tuple(output)

    @staticmethod
    def _owned_camera(client: BillingClient, camera_id: str) -> BillingService:
        if not re.fullmatch(r"[1-9][0-9]{0,17}", camera_id):
            raise BillingValidationError("Некорректный ID камеры.")
        return next(
            (s for s in client.services
             if s.id == camera_id and s.camera_id == camera_id),
            None
        ) or (_ for _ in ()).throw(
            BillingPermissionDenied("Камера не принадлежит выбранному аккаунту.")
        )

    def set_camera_status(
        self, client: BillingClient, camera_id: str,
        status: int, effective_date,
    ) -> tuple[dict, BillingClient | None]:
        """User-confirmed camera activation/deactivation only."""
        from datetime import date
        service = self._owned_camera(client, camera_id)
        if type(status) is not int or status not in (0, 1):
            raise BillingValidationError("Неподдерживаемый статус.")
        # Status is based on the verified 0 (disabled) / 1 (enabled) CRM codes.
        parsed = self._transport.put_camera_status(
            service.id, status, effective_date
        )
        if parsed.get("success") is not True or parsed.get("response") != 1:
            raise BillingWriteOutcomeUnknown(
                "Не получено ожидаемое подтверждение смены статуса. "
                "Проверьте биллинг перед повтором."
            )
        try:
            fresh = self.refresh_client(client)
        except Exception:
            fresh = None
        return parsed, fresh

    def transfer_camera(
        self, client: BillingClient, camera_id: str, recipient: BillingSearchHit,
        *, move_in_vc: bool = False,
    ) -> tuple[dict, BillingClient | None]:
        """Transfer billing ownership to a separately verified account ID.

        The captured operation is only for move_in_vc='0'. It does not imply
        physical B2O camera reparenting; require more evidence for that option.
        """
        self._owned_camera(client, camera_id)
        if not isinstance(recipient, BillingSearchHit):
            raise BillingValidationError("Выберите аккаунт из результатов поиска.")
        if client.id == recipient.id:
            raise BillingValidationError("Аккаунт получателя совпадает с текущим.")
        if client.country != recipient.country:
            raise BillingPermissionDenied(
                "Перенос между странами не подтверждён API."
            )
        # Read back the exact selected recipient before mutating the camera.
        confirmed = self.find_client_by_hit(recipient)
        if confirmed.id != recipient.id or confirmed.country != recipient.country:
            raise BillingPermissionDenied("Получатель изменился. Повторите поиск.")
        response = self._transport.put_camera_owner(
            camera_id, client.id, recipient.id, move_in_vc=move_in_vc
        )
        # Observed contract: {"success":true,"response":0}.
        # Zero is not a failure and must not be interpreted as falsy.
        if response.get("success") is not True or (
            type(response.get("response")) is not int or response["response"] != 0
        ):
            raise BillingWriteOutcomeUnknown(
                "Биллинг не вернул ожидаемое подтверждение передачи. "
                "Проверьте владельца камеры перед повтором."
            )
        try:
            fresh = self.refresh_client(client)
        except Exception:
            fresh = None
        return response, fresh

    def refresh_client(self, client: BillingClient) -> BillingClient:
        """Read current camera services by the already selected stable CRM ID."""
        if not re.fullmatch(r"[1-9][0-9]{0,17}", client.id):
            raise BillingValidationError("Некорректный ID клиента для обновления.")
        return attach_cameras_to_client(
            client, self._get_json(f"/api/accounts/{client.id}/cameras")
        )

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
        # Reload by the confirmed account ID, NOT by login: one login may
        # belong to several countries and a new search would be ambiguous.
        # Scheduled tariffs may not be shown until their effective date.
        try:
            fresh = self.refresh_client(client)
        except Exception:
            fresh = None
        return TariffChangeReceipt(
            camera_id=receipt.camera_id, tariff_id=receipt.tariff_id,
            message=receipt.message, remain_pay=receipt.remain_pay,
            updated_client=fresh if fresh is not None and fresh.id == client.id else None,
        )
