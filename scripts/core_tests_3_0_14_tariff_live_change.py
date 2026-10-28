from __future__ import annotations

"""Observed CRM PUT: fictional credentials, no network, no production mutation."""

import os
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingPermissionDenied, BillingService,
    BillingValidationError, validate_tariff_change, TariffOption,
)
from linkvideo_vpn_helper.services.billing_catalog import (
    TariffCatalogEntry, TariffReferenceCatalog, BillingCountry,
)
from linkvideo_vpn_helper.services.billing_editable_gateway import (
    BillingEditableGateway, TariffChangeReceipt, validated_tariff_receipt,
)
from linkvideo_vpn_helper.services.billing_token_transport import (
    BillingTokenTransport, BillingWriteOutcomeUnknown, BillingWriteRejected,
)
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffCatalogDialog, TariffsPage

country = BillingCountry("3", "KZ", "Казахстан", "тг.", "lkz")
service = BillingService(
    id="304", camera_id="304", camera_name="Тестовая камера",
    tariff_name="Стартовый 90", price_text="0",
    discount_text="0", total_text="0", status="Активный",
)
client = BillingClient(
    id="333", login="lkz_fake_001", country="Казахстан",
    status="Активный", legal_type="Физическое лицо",
    services=(service,), services_loaded=True,
)
catalog = TariffReferenceCatalog(
    tariffs=(
        TariffCatalogEntry("230", "ЮЛ 7 дней, 1 Мбит/с", "Казахстан",
                           "Физическое лицо", Decimal("3680")),
        TariffCatalogEntry("240", "ЮЛ 30 дней, 0,5 Мбит/с", "Казахстан",
                           "Физическое лицо", Decimal("6700")),
        TariffCatalogEntry("900", "BYN 7 дней", "Беларусь",
                           "Физическое лицо", Decimal("99")),
        TariffCatalogEntry("999", "KZT 30 дней", "Казахстан",
                           "Юридическое лицо", Decimal("45")),
    ),
    archive_periods=(), country=country,
)
day = date.today() + timedelta(days=4)
change = validate_tariff_change(
    client, service, TariffOption("230", "ЮЛ 7 дней, 1 Мбит/с"), day, "",
)

class Response:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body
    def json(self):
        return self.body

class Session:
    def __init__(self):
        self.calls = []
        self.answer = Response(200, {
            "success": True,
            "response": {
                "result": "8",
                "message": "Тариф тестовой камеры будет изменён с будущей даты",
                "remain_pay": 0,
            },
        })
    def put(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.answer
    def close(self):
        pass

session = Session()
transport = BillingTokenTransport(
    "fictional-token", header_mode="token", _session=session
)
gateway = BillingEditableGateway(transport)
gateway.reference_catalog = lambda given: catalog
gateway.find_client = lambda given: client

receipt = gateway.change_tariff(change, client=client)
assert isinstance(receipt, TariffChangeReceipt)
assert receipt.camera_id == "304" and receipt.tariff_id == "230"
assert receipt.remain_pay == "0"
assert receipt.updated_client is None  # No GET configured in fake PUT-only session
assert len(session.calls) == 1
url, request = session.calls[0]
assert url == "https://api-billing-global.linkvideo.ru/api/cameras/304/tariff/230"
assert request["json"] == {"date": day.strftime("%d-%m-%Y")}
assert request["headers"]["token"] == "fictional-token"
assert request["allow_redirects"] is False
assert "fictional-token" not in repr(receipt)

for invalid in (
    {"tariff_id": "900"},
    {"service_id": "305"}, {"client_id": "334"}, {"tariff_id": "../240"},
):
    from dataclasses import replace
    bad = replace(change, **invalid)
    before = len(session.calls)
    try:
        gateway.change_tariff(bad, client=client)
    except (BillingValidationError, BillingPermissionDenied):
        pass
    else:
        raise AssertionError(f"Unexpected write for {invalid}")
    assert len(session.calls) == before

session.answer = Response(400, {"success": False, "message": "secret"})
try:
    gateway.change_tariff(change, client=client)
except BillingWriteRejected as exc:
    assert exc.status_code == 400
    assert "secret" not in str(exc)
else:
    raise AssertionError("HTTP 400 not rejected")

session.answer = Response(503, {"success": False})
before = len(session.calls)
try:
    gateway.change_tariff(change, client=client)
except BillingWriteOutcomeUnknown:
    pass
else:
    raise AssertionError("503 outcome should be unknown")
assert len(session.calls) == before + 1, "Never retry PUT"

session.answer = Response(200, {"success": True, "response": {
    "result": "8", "message": "Дата подтверждена", "remain_pay": 0,
}})
assert validated_tariff_receipt(session.answer.json(), change).message == "Дата подтверждена"
for invalid in ({}, {"success": True, "response": {}}, {"success": False}):
    try:
        validated_tariff_receipt(invalid, change)
    except (BillingWriteOutcomeUnknown, BillingValidationError):
        pass
    else:
        raise AssertionError("Missing server confirmation not detected")

app = QApplication.instance() or QApplication([])
dialog = TariffCatalogDialog(catalog, client.legal_type, service=service)
assert dialog.tariff_cards.count() == 3
assert not dialog.apply_tariff.isEnabled()
assert dialog.findChild(QPushButton, "TariffQuickDays7")
dialog.findChild(QPushButton, "TariffQuickDays7").click()
assert dialog.tariff_cards.count() == 1
dialog.tariff_cards.setCurrentRow(0)
assert dialog.apply_tariff.isEnabled()
assert dialog.selection_change(client).tariff_id == "230"
assert dialog.selection_change(client).effective_date >= date.today()
dialog.bitrate_filter.setCurrentIndex(dialog.bitrate_filter.findData(0.5))
assert dialog.tariff_cards.count() == 0
assert not dialog.apply_tariff.isEnabled()
dialog.close()

page = TariffsPage(gateway=gateway)
page.client = client
page._render_client(client)
assert page.findChild(QPushButton, "BillingCameraChangeTariff").isEnabled()
assert not page.services_table.isVisible()
assert page.camera_scroll.isVisible() is False or page.camera_scroll.isVisible() is True
assert page.page_scroll.verticalScrollBar().singleStep() == 24
called = []
with patch.object(page, "_apply_service_tariff", side_effect=lambda *args: called.append(args)):
    page._apply_service_tariff(service, change, catalog.tariffs)
assert len(called) == 1
page.close()
app.processEvents()
print("CORE TESTS BILLING PUT SINGLE-SHOT, RECEIPT, CUSTOMER/COUNTRY AND NEW PICKER OK")
