from __future__ import annotations

"""Global catalog /list/0 with dynamic entries and no tariff writes."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from linkvideo_vpn_helper.services.billing_catalog import (
    parse_all_tariffs_snapshot,
)
from linkvideo_vpn_helper.services.billing_readonly_gateway import BillingReadOnlyGateway
from linkvideo_vpn_helper.ui.billing_all_tariffs import BillingAllTariffsWidget

tariff_list = {"success": True, "response": [
    {
        "id_tariff": 101, "tariff_name": "Тест Беларусь", "country": "Беларусь",
        "account_type_name": "Физическое лицо", "full_ab_plata": 75,
        "test_days": 0, "count_cameras": 2, "is_tariff_lv_monitor": 0,
    },
    {
        "id_tariff": 102, "tariff_name": "Тест Казахстан", "country": "Казахстан",
        "account_type_name": "Юридическое лицо", "full_ab_plata": 555,
        "test_days": 7, "count_cameras": None, "is_tariff_lv_monitor": 0,
    },
]}
countries = {"success": True, "response": [
    {"id_country": "2", "code": "BY", "name": "Беларусь",
     "currency_code": "BYN", "prefix_login": "lrb"},
    {"id_country": "3", "code": "KZ", "name": "Казахстан",
     "currency_code": "тг.", "prefix_login": "lkz"},
]}
routes = []
payloads = {
    "/api/tariffs/list/0": tariff_list,
    "/api/countries/1": countries,
}
def read(path):
    routes.append(path)
    return payloads[path]

gateway = BillingReadOnlyGateway(read)
assert not gateway.can_change_tariffs
snapshot = gateway.all_tariffs()
assert len(snapshot.tariffs) == 2
assert routes == ["/api/tariffs/list/0", "/api/countries/1"]

app = QApplication.instance() or QApplication([])
widget = BillingAllTariffsWidget(gateway)
assert widget.refresh.isEnabled()
widget._on_result(0, snapshot, None)
assert widget.table.rowCount() == 2
assert widget.table.item(0, 4).text() == "75 BYN"
assert widget.table.item(1, 4).text() == "555 тг."
widget.search.setText("Казахстан")
assert not widget.table.isRowHidden(1)
# Text filter searches name, description and ID, not country.
widget.search.setText("Тест Казахстан")
assert widget.table.isRowHidden(0) and not widget.table.isRowHidden(1)
widget.search.clear()
widget.country.setCurrentIndex(1)
assert not widget.table.isRowHidden(0) and widget.table.isRowHidden(1)

# New server tariff appears after a future reload without Helper updates.
tariff_list["response"].append({
    "id_tariff": 103, "tariff_name": "Новый тариф", "country": "Беларусь",
    "account_type_name": "Физическое лицо", "full_ab_plata": 88,
})
widget._on_result(0, gateway.all_tariffs(), None)
assert widget.table.rowCount() == 3
assert widget.table.item(2, 1).text() == "Новый тариф"
assert widget.table.item(2, 4).text() == "88 BYN"
widget.close()
app.processEvents()
print("CORE TESTS BILLING ALL TARIFFS DYNAMIC CATALOG OK")
