from __future__ import annotations

"""UI geometry, regional VPN selection and parent-wheel regressions.

No live network or billing mutations.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QLabel, QListWidget, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)
from linkvideo_vpn_helper.services.billing_gateway import BillingClient, BillingService
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.theme import get_theme_style
from linkvideo_vpn_helper.ui.nested_scroll_guard import _NestedScrollFilter
from linkvideo_vpn_helper.ui.pages.create_client_page import CreateClientPage
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage

app = QApplication.instance() or QApplication([])
service = BillingService(
    id="1", camera_name="Пример камера", camera_id="2",
    tariff_name="Тестовый", price_text="0", discount_text="0",
    total_text="0", status="Активный",
)
client = BillingClient(
    id="42", login="lkz_demo_test", country="Казахстан",
    status="Активный", services=(service,), services_loaded=True,
)

with tempfile.TemporaryDirectory() as temp:
    settings = QSettings(str(Path(temp) / "test.ini"), QSettings.Format.IniFormat)
    registry = ServerRegistry(settings)
    creator = CreateClientPage(None, None, None, registry)
    for country, prefix in (
        ("Россия", "vpn"), ("Казахстан", "kz-vpn"), ("Беларусь", "rb-vpn")
    ):
        creator.set_country(country)
        assert creator.country_picker.currentData() == country
        assert registry.get(creator.server_picker.host()).country == country
        assert creator._country_hosts(country)
        assert creator.server_picker.host().startswith(prefix)
    creator.close()

    for theme in ("rose_milk", "linkvideo_2026", "lavender_mist",
                  "ocean_blue", "midnight_purple", "cherry_pink", "soft_gray"):
        app.setStyleSheet(get_theme_style(theme))
        page = TariffsPage(settings=settings)
        page.resize(1060, 820)
        page.show()
        page.client = client
        page._render_client(client)
        app.processEvents()
        assert page.workspace_tabs.count() == 3
        assert page.workspace_tabs.tabText(1) == "Создать клиента"
        assert page.services_table.columnWidth(6) >= 184
        controls = page.services_table.cellWidget(0, 6)
        assert controls is not None
        button = controls.findChild(QPushButton, "TariffChangeButton")
        assert button is not None and button.minimumWidth() >= 100
        assert button.fontMetrics().horizontalAdvance(button.text()) < button.minimumWidth()
        assert not page.b2o_creator.create_button.isEnabled()
        assert page.b2o_creator.country.count() == 3
        page.close()

    outer = QScrollArea()
    outer.resize(400, 280)
    outer.setWidgetResizable(True)
    content = QWidget()
    vertical = QVBoxLayout(content)
    inner = QListWidget()
    inner.setFixedHeight(150)
    for n in range(30):
        inner.addItem("Порт " + str(n))
    vertical.addWidget(QLabel("Начало"))
    vertical.addWidget(inner)
    bottom = QLabel("Низ страницы")
    bottom.setFixedHeight(1200)
    vertical.addWidget(bottom)
    outer.setWidget(content)
    outer.show()
    app.processEvents()
    outer.verticalScrollBar().setValue(0)
    inner.verticalScrollBar().setValue(inner.verticalScrollBar().maximum())

    wheel = QWheelEvent(
        QPointF(15, 15), QPointF(15, 15),
        QPoint(0, 0), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    filter_ = _NestedScrollFilter()
    assert filter_.eventFilter(inner.viewport(), wheel)
    assert outer.verticalScrollBar().value() > 0, (
        "A nested list at its edge must scroll the parent page"
    )
    outer.close()

print("CORE TESTS 3.0.14 CLIENTS/TARIFFS LAYOUT AND REGIONAL VPN OK")
