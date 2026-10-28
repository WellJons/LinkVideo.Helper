from __future__ import annotations

"""VPN split layout and billing multi-camera actions: all test data fictional."""

import os
import tempfile
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QCheckBox, QPushButton

from linkvideo_vpn_helper.services.vpn_service import (
    ClientRecord, SessionCredentials,
)
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingService, TariffOption, validate_tariff_change,
)
from linkvideo_vpn_helper.services.billing_catalog import (
    BillingCountry, TariffCatalogEntry, TariffReferenceCatalog,
)
from linkvideo_vpn_helper.services.billing_editable_gateway import TariffChangeReceipt
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as folder:
    settings = QSettings(str(Path(folder) / "ui.ini"), QSettings.Format.IniFormat)
    registry = ServerRegistry(settings)
    page = SearchManagePage(
        service=object(), search=object(),
        credentials=SessionCredentials("example", "fake"),
        registry=registry,
    )
    page.current = ClientRecord(
        server="vpn02.linkvideo.ru", login="fictional_01", password="fictional-only",
        remote_address="172.16.2.133", ports=[11690,11691,11692],
        is_online=False, active_ports=[11690, 11691],
    )
    page._open_client_view()
    app.processEvents()
    assert page.left_card.isHidden(), "Other accounts must be covered"
    assert not page.client_info_card.isHidden(), "Profile must appear at left"
    assert not page.detail_panel.isHidden(), "Ports and controls must appear right"
    assert page.client_info_card.objectName() == "SearchClientInfoCard"
    assert page.detail_scroll.objectName() == "SearchClientControlsScroll"
    assert page.info_scroll.verticalScrollBar().singleStep() == 28
    assert page.detail_scroll.verticalScrollBar().singleStep() == 28
    assert page.port_list.count() == 3
    assert page.toggle_port_button is not None
    page._close_client_view()
    assert not page.left_card.isHidden()
    assert page.client_info_card.isHidden()
    assert page.detail_panel.isHidden()
    page.close()

camera_a = BillingService(
    "304", "Камера 1", "304", "Стартовый 90", "0", "0", "0", "Активный",
)
camera_b = BillingService(
    "305", "Камера 2", "305", "ЮЛ 30 дней", "0", "0", "0", "Активный",
)
client = BillingClient(
    "333", "fictional_client", "Казахстан", "Активный",
    legal_type="Физическое лицо", services=(camera_a,camera_b),
    services_loaded=True,
)
catalog = TariffReferenceCatalog(
    tariffs=(
        TariffCatalogEntry(
            "230", "ЮЛ 7 дней, 1 Мбит/с", "Казахстан",
            "Юридическое лицо", Decimal("3680")
        ),
    ),
    archive_periods=(),
    country=BillingCountry("3", "KZ", "Казахстан", "тг.", "lkz"),
)

class FakeGateway:
    configured = True
    can_change_tariffs = True
    def __init__(self):
        self.calls = []
    def reference_catalog(self, who):
        assert who.id == "333"
        return catalog
    def change_tariff(self, change, *, client):
        assert client.id == "333"
        self.calls.append((change.service_id, change.tariff_id))
        return TariffChangeReceipt(
            change.service_id, change.tariff_id,
            "Назначен с выбранной даты", "0"
        )
    def refresh_client(self, who):
        return who

gateway = FakeGateway()
billing = TariffsPage(gateway=gateway)
billing.client = client
billing._render_client(client)
checks = billing.findChildren(QCheckBox, "BillingCameraSelect")
assert len(checks) == 2
assert billing.findChild(QPushButton, "BillingCameraOtherActions") is not None
assert billing.findChild(QPushButton, "BulkTariffChange") is billing.bulk_btn
assert not billing.bulk_btn.isEnabled()
checks[0].setChecked(True)
checks[1].setChecked(True)
assert billing.bulk_btn.isEnabled()
assert "(2)" in billing.bulk_btn.text()
change = validate_tariff_change(
    client, camera_a, TariffOption("230", "ЮЛ 7 дней, 1 Мбит/с"),
    date.today() + timedelta(days=3), "",
)
with patch("linkvideo_vpn_helper.ui.pages.tariffs_page.ConfirmDialog.exec",
           return_value=QDialog.DialogCode.Accepted):
    billing._apply_bulk_tariff((camera_a,camera_b), change, catalog.tariffs)
for _ in range(160):
    app.processEvents()
    if not billing._busy:
        break
    time.sleep(0.02)
assert not billing._busy, "Bulk operation must complete without UI freeze"
assert gateway.calls == [("304","230"), ("305","230")]
assert "Подтверждено камер: 2" in billing.task.detail.text() if hasattr(billing.task, "detail") else len(gateway.calls) == 2
billing.close()
app.processEvents()
print("CORE TESTS VPN SPLIT CARD AND BILLING MULTI CAMERA SAFE CHANGE OK")
