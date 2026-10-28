from __future__ import annotations

"""Tariffs integration stage one: real CRM link, no invented billing data."""

import ast
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
page_path = root / "linkvideo_vpn_helper/ui/pages/tariffs_page.py"
main_path = root / "linkvideo_vpn_helper/ui/main_window.py"
nav_path = root / "linkvideo_vpn_helper/ui/nav_icons.py"
page_source = page_path.read_text(encoding="utf-8")
main_source = main_path.read_text(encoding="utf-8")
nav_source = nav_path.read_text(encoding="utf-8")

for path, source in (
    (page_path, page_source),
    (main_path, main_source),
    (nav_path, nav_source),
):
    ast.parse(source, filename=str(path))

assert 'BILLING_LOGIN_URL = "https://crm.linkvideo.ru/login"' in page_source
assert "QDesktopServices.openUrl(QUrl(BILLING_LOGIN_URL))" in page_source
assert "API не подключён" in page_source
assert "QWebEngineView" not in page_source
assert "requests.post" not in page_source
assert "password" not in page_source.lower().split("class TariffsPage", 1)[-1]
assert '("tariffs", "₽", "Тарифы"' in main_source
assert 'elif key == "tariffs":' in main_source
assert '"tariffs":' in nav_source

# A live browser may already be authenticated. Do not open it in CI, perform
# login or transmit any credentials; verify the rendering without network IO.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage

app = QApplication.instance() or QApplication([])
page = TariffsPage()
assert page.page_layout is not None
assert page.api_status.text() == "API не подключён"
assert isinstance(page.open_billing, QPushButton)
assert page.open_billing.text() == "Открыть биллинг"
assert page.open_billing.isEnabled()
assert not page.task.isVisible()
assert page.findChildren(QPushButton) == [page.open_billing]
page.close()
app.processEvents()
print("CORE TESTS TARIFFS PLACEHOLDER SAFE CRM LAUNCH OK")
