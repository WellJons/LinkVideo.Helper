from __future__ import annotations

"""CRM tariff preview: no live writes, real Qt widgets with fictional data."""

import os
from datetime import date
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from linkvideo_vpn_helper.services.billing_catalog import (
    AllTariffsSnapshot, ArchivePeriod, BillingCountry,
    TariffCatalogEntry, TariffReferenceCatalog,
)
from linkvideo_vpn_helper.services.billing_gateway import BillingClient, BillingService
from linkvideo_vpn_helper.services.billing_tariff_filter import (
    archive_days_from_name, country_tariff_visible, select_tariffs,
)
from linkvideo_vpn_helper.ui.billing_all_tariffs import BillingAllTariffsWidget
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffCatalogDialog, TariffsPage


def tariff(ident: str, name: str, country="Беларусь", account_type="Юридическое лицо"):
    return TariffCatalogEntry(
        id=ident, name=name, country=country,
        account_type=account_type, price=Decimal("30.0"),
        test_days=2,
    )


rows = (
    tariff("90", "BYN ЮЛ 90 дней"),
    tariff("10", "BYN ЮЛ 10 дней"),
    tariff("50", "KZT ЮЛ 50 дней"),  # Country field alone is not sufficient.
    tariff("x", "BYN ЮЛ без архива"),
    tariff("30", "BYN ЮЛ 30 дней"),
    tariff("mix", "BYN KZT ЮЛ 20 дней"),
    tariff("kz", "KZT ЮЛ 7 дней", "Казахстан"),
)
assert archive_days_from_name("ЮЛ 30 дней") == 30
assert archive_days_from_name("Заявка 14 дней") == 14
assert archive_days_from_name("Tariff 44") is None
assert archive_days_from_name("30 дней и 90 дней") is None
assert archive_days_from_name("тест 30 дней") == 30
assert archive_days_from_name("Без архива") is None
assert country_tariff_visible(rows[0], "Беларусь")
assert not country_tariff_visible(rows[2], "Беларусь")
assert not country_tariff_visible(rows[-1], "Беларусь")
assert [r.id for r in select_tariffs(rows, country="Беларусь")] == [
    "10", "30", "90", "x"
]
assert [r.id for r in select_tariffs(rows, country="Беларусь", days=30)] == ["30"]
assert [r.id for r in select_tariffs(
    rows, country="Беларусь", account_type="Физическое лицо"
)] == []
assert [r.id for r in select_tariffs(rows, country="Казахстан")] == ["kz"]
assert [r.id for r in select_tariffs(rows, country="Беларусь", search="90")] == ["90"]
# Trial length test_days=2 must not be treated as retention.
assert select_tariffs(rows, country="Беларусь", days=2) == ()

country = BillingCountry(
    id="2", code="BY", name="Беларусь", currency_code="BYN", prefix_login="lrb"
)
service = BillingService(
    id="svc-1", camera_name="Камера 1", camera_id="44",
    tariff_name="BYN ЮЛ 10 дней", price_text="10",
    discount_text="0", total_text="10", status="Активный",
)
client = BillingClient(
    id="101", login="test_byn", country="Беларусь", status="Активный",
    legal_type="Юридическое лицо", services=(service,), services_loaded=True,
)
snapshot = TariffReferenceCatalog(
    tariffs=rows,
    archive_periods=(
        ArchivePeriod("1", "90 дней", 90),
        ArchivePeriod("2", "10 дней", 10),
    ), country=country,
)

app = QApplication.instance() or QApplication([])
dialog = TariffCatalogDialog(snapshot, client.legal_type, service=service)
assert dialog.table.rowCount() == 4
assert dialog.table.item(0, 0).text() == "BYN ЮЛ 10 дней"
assert dialog.table.item(0, 1).text() == "10 дн."
assert dialog.table.item(2, 0).text() == "BYN ЮЛ 90 дней"
assert dialog.period_table.item(0, 1).text() == "10"
assert dialog.table.item(0, 4).text() == "2"  # Trial, not retention.
assert dialog.findChild(QPushButton, "TariffPreviewDateButton")
dialog.archive_days.setValue(30)
assert dialog.table.rowCount() == 1
assert dialog.table.item(0, 0).text() == "BYN ЮЛ 30 дней"
dialog.table.selectRow(0)
message = dialog.selection_message()
assert "Камера 1" in message and "BYN ЮЛ 30 дней" in message
assert "Тариф ещё не изменён" in message
dialog.copy_selection.click()
assert QApplication.clipboard().text() == message
with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
    dialog._open_date_calendar()
assert dialog.pick_date.text() == QDate.currentDate().toString("dd.MM.yyyy")
dialog.close()

class FakeGateway:
    configured = True
    can_change_tariffs = False

    def reference_catalog(self, client):
        return snapshot

    def all_tariffs(self):
        return AllTariffsSnapshot(rows, (country,))

gateway = FakeGateway()
all_tariffs = BillingAllTariffsWidget(gateway)
all_tariffs._on_result(
    all_tariffs._query,
    AllTariffsSnapshot(rows, (country,)), None,
)
assert all_tariffs.table.rowCount() == len(rows)
all_tariffs.country.setCurrentIndex(all_tariffs.country.findData("Беларусь"))
assert all_tariffs.table.rowCount() == 4
assert all_tariffs.table.item(0, 1).text() == "BYN ЮЛ 10 дней"
all_tariffs.archive_days.setValue(90)
assert all_tariffs.table.rowCount() == 1
assert all_tariffs.table.item(0, 1).text() == "BYN ЮЛ 90 дней"
all_tariffs.close()

page = TariffsPage(gateway=gateway)
page.client = client
page._render_client(client)
button = page.findChild(QPushButton, "TariffChangeButton")
assert button is not None and button.isEnabled()
called = []
with patch.object(page, "_request_service_catalog", side_effect=lambda srv: called.append(srv)):
    button.click()
assert called == [service], "Tariff button must actually open customer-scoped read-only selector"
page.close()
app.processEvents()
print("CORE TESTS TARIFF CLIENT DAYS/CURRENCY SORT, CALENDAR AND COPY PREVIEW OK")
