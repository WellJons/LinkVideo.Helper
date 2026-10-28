from __future__ import annotations

"""Billing workbench: domain contract, safe UI, and per-service change modal."""

import os
from dataclasses import replace
from datetime import date, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from linkvideo_vpn_helper.services.billing_gateway import (
    ALLOWED_COUNTRIES, BillingClient, BillingNotConfigured,
    BillingPermissionDenied, BillingService, BillingValidationError, TariffOption,
    UnconfiguredBillingGateway, validate_client, validate_login,
    validate_tariff_change,
)
from linkvideo_vpn_helper.ui.pages.tariffs_page import ChangeTariffDialog, TariffsPage


assert ALLOWED_COUNTRIES == {"Беларусь", "Казахстан"}
assert validate_login("  lkz_demo_0001  ") == "lkz_demo_0001"
for bad_login in ("", "a", "wrong login", "x" * 101):
    try:
        validate_login(bad_login)
        raise AssertionError("Unsafe login accepted")
    except BillingValidationError:
        pass

service = BillingService(
    id="svc_test_1", camera_name="Тестовая камера", camera_id="cam_test_1",
    tariff_name="Тестовый тариф A", price_text="100", discount_text="0",
    total_text="100", status="Активный",
)
client = BillingClient(
    id="client_test_1", login="lkz_demo_0001", country="Казахстан",
    status="Активный", contract_number="contract_test_1",
    balance_text="0", services=(service,),
)
assert validate_client(client) is client
assert validate_client(replace(client, country="Беларусь")).country == "Беларусь"
try:
    validate_client(replace(client, country="Россия"))
    raise AssertionError("Russia should not be included until its database is connected")
except BillingPermissionDenied:
    pass

option = TariffOption("tariff_test_b", "Тестовый тариф B")
change = validate_tariff_change(client, service, option, date.today(), "Тест")
assert change.service_id == "svc_test_1" and change.tariff_id == option.id
assert change.client_id == client.id and change.comment == "Тест"
for service_arg, day, comment in (
    (replace(service, id="another_service"), date.today(), ""),
    (service, date.today() - timedelta(days=1), ""),
    (service, date.today(), "x" * 501),
):
    try:
        validate_tariff_change(client, service_arg, option, day, comment)
        raise AssertionError("Invalid tariff change should not pass")
    except BillingValidationError:
        pass

default_gateway = UnconfiguredBillingGateway()
assert not default_gateway.configured and not default_gateway.can_change_tariffs
try:
    default_gateway.find_client("lkz_demo_0001")
    raise AssertionError("Default gateway must fail closed")
except BillingNotConfigured:
    pass

class FakeGateway:
    configured = True
    can_change_tariffs = False

    def find_client(self, login):
        assert login == client.login
        return client

    def tariff_options(self, client_id, service_id):
        return (TariffOption("tariff_test_a", "Тестовый тариф A"), option)

    def change_tariff(self, change):
        raise AssertionError("Read-only provider must not be allowed to mutate data")

app = QApplication.instance() or QApplication([])
page = TariffsPage(gateway=FakeGateway())
assert page.api_status.text() == "Подключён"
assert page.tabs.count() == 4
page._on_search(0, client, None)
assert page.client == client
assert page.services_table.rowCount() == 1
assert page.services_table.item(0, 0).text().startswith("Тестовая камера")
assert page.metric_values["services"].text() == "1"
action = page.services_table.cellWidget(0, 6)
assert isinstance(action, QPushButton)
assert not action.isEnabled()
assert "API" in action.toolTip()
assert page.info_values["country"].text() == "Казахстан"

dialog = ChangeTariffDialog(service, (TariffOption("tariff_test_a", service.tariff_name), option))
assert dialog.tariff.count() == 2
dialog.tariff.setCurrentIndex(1)
selected, day, note = dialog.selection()
assert selected.id == option.id
assert day >= date.today() and not note
assert dialog.apply.isEnabled()
dialog.close()

# Even with a connected provider, country limitation is enforced when a
# result comes back. No customer record from another database may be opened.
try:
    page._render_client(replace(client, country="Россия"))
    raise AssertionError("Unapproved country exposed")
except BillingPermissionDenied:
    pass
page.close()
app.processEvents()
print("CORE TESTS BILLING DOMAIN AND UI WORKFLOW OK")
