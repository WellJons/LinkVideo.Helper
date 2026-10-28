from __future__ import annotations

"""CRM reference data: real field names, fictional data, no API credentials."""

from dataclasses import replace
from decimal import Decimal
import os

from linkvideo_vpn_helper.services.billing_catalog import (
    build_tariff_reference, filter_tariffs_for_client,
    parse_archive_periods, parse_countries, parse_tariff_catalog,
)
from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingValidationError,
)
from linkvideo_vpn_helper.services.billing_readonly_gateway import BillingReadOnlyGateway

def tariff(ident, country, *, price=10, account_type="Физическое лицо",
           monitor=0, shown=0, cameras=None):
    return {
        "id_tariff": ident,
        "tariff_name": "Тариф " + str(ident),
        "description": "Тестовое описание",
        "country": country,
        "account_type_name": account_type,
        "full_ab_plata": price,
        "is_start": 3,
        "test_days": 0,
        "id_tariff_vc": None,
        "count_cameras": cameras,
        "start_tariff_lv_monitor": None,
        "is_tariff_lv_monitor": monitor,
        "is_showed": shown,
    }

tariffs = {"success": True, "response": [
    tariff(1, "Беларусь", price=19, shown=1, cameras=2),
    tariff(2, "Казахстан", price=3680, shown=1, cameras=None),
    tariff(3, "Казахстан", price=0, monitor=1, shown=0, cameras=8),
    tariff(4, "Российская федерация", price=100, shown=1),
    tariff(5, "Казахстан", account_type="Юридическое лицо", price=200, shown=1),
]}
archives = {"success": True, "response": [
    {"id_archive_period": "1", "archive_period_name": "180 дней", "days": 180},
    {"id_archive_period": "6", "archive_period_name": "0 дней", "days": 0},
    {"id_archive_period": "11", "archive_period_name": "90 дней", "days": 90},
]}
countries = {"success": True, "response": [
    {"id_country": "1", "code": "RU", "name": "Российская федерация",
     "currency_code": "руб.", "prefix_login": "lrf"},
    {"id_country": "2", "code": "BY", "name": "Беларусь",
     "currency_code": "BYN", "prefix_login": "lrb"},
    {"id_country": "3", "code": "KZ", "name": "Казахстан",
     "currency_code": "тг.", "prefix_login": "lkz"},
]}
parsed = parse_tariff_catalog(tariffs)
assert len(parsed) == 5
assert parsed[0].id == "1" and parsed[0].price == Decimal("19")
assert parsed[1].count_cameras is None and parsed[1].is_showed == 1
assert parsed[2].is_monitor and parsed[2].is_showed == 0
assert parsed[2].is_start == 3 and parsed[2].test_days == 0
assert len(parse_archive_periods(archives)) == 3
assert parse_archive_periods(archives)[1].days == 0
assert len(parse_countries(countries)) == 3
assert parse_countries(countries)[2].currency_code == "тг."

client = BillingClient(
    id="300", login="lkz_demo_00001", country="Казахстан",
    status="Активный", legal_type="Физическое лицо",
)
scoped = filter_tariffs_for_client(parsed, client)
assert [x.id for x in scoped] == ["2", "3"]
# Monitor and is_showed data are preserved without guessing edit eligibility.
assert scoped[1].is_monitor and scoped[1].is_showed == 0
assert len(filter_tariffs_for_client(parsed, replace(client, country="Беларусь"))) == 1
try:
    filter_tariffs_for_client(parsed, replace(client, legal_type=""))
    raise AssertionError("Missing account type allowed broad catalog")
except BillingValidationError:
    pass
for payload in (
    {"success": False, "response": []},
    {"success": True, "response": {}},
    {"success": True, "response": [tariff(1, "Казахстан", price="-1")]},
    {"success": True, "response": [tariff(1, "Казахстан", price="NaN")]},
    {"success": True, "response": [tariff(1, "Казахстан", price=True)]},
    {"success": True, "response": [tariff(1, "Казахстан")] * 2},
    {"success": True, "response": [tariff(1, "Казахстан", monitor=9)]},
):
    try:
        parse_tariff_catalog(payload)
        raise AssertionError("Invalid tariff payload accepted")
    except BillingValidationError:
        pass

calls = []
data = {
    "/api/tariffs/list/1": tariffs,
    "/api/cameras/archive-depth/1": archives,
    "/api/countries/1": countries,
}
def fake_get(path):
    calls.append(path)
    return data[path]

gateway = BillingReadOnlyGateway(fake_get)
reference = gateway.reference_catalog(client)
assert reference.country.name == "Казахстан"
assert reference.country.currency_code == "тг."
assert [x.id for x in reference.tariffs] == ["2", "3"]
assert [x.days for x in reference.archive_periods] == [180, 0, 90]
assert calls == [
    "/api/tariffs/list/1", "/api/cameras/archive-depth/1", "/api/countries/1",
]
assert not gateway.can_change_tariffs

# Offscreen UI constructs a real, read-only searchable catalog.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffCatalogDialog, TariffsPage
app = QApplication.instance() or QApplication([])
dialog = TariffCatalogDialog(reference, client.legal_type)
assert dialog.table.rowCount() == 2
assert dialog.period_table.rowCount() == 3
assert dialog.table.item(0, 1).text() == "3680 тг."
dialog.filter.setText("Тариф 3")
assert dialog.table.isRowHidden(0) and not dialog.table.isRowHidden(1)
dialog.close()

page = TariffsPage(gateway=gateway)
assert not page.catalog_btn.isEnabled()  # cannot search before selecting a client
page._render_client(client)
assert page.catalog_btn.isEnabled()
page._set_busy(True)
assert not page.catalog_btn.isEnabled()
page._set_busy(False)
assert page.catalog_btn.isEnabled()
page.close()
app.processEvents()
print("CORE TESTS BILLING TARIFF/ARCHIVE/COUNTRY CATALOG OK")
