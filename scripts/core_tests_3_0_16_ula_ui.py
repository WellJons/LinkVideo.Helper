from __future__ import annotations

"""Offline Ula login configuration and Qt UI regression; no Oracle network."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTableWidget

from linkvideo_vpn_helper.services.ula_connection import (
    UlaConnectionDetails, read_ula_ini,
)
from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaClientHit, UlaService
from linkvideo_vpn_helper.ui.ula_services_tab import UlaServicesTab, _safe_error


with tempfile.TemporaryDirectory() as folder:
    ini = Path(folder, "Ula.ini")
    ini.write_text(
        "[DB]\nUserName=fictional\nServer=127.0.0.1\nPort=1521\nSid=TEST\n"
        "[Settings]\nCountMonitor=2\n", encoding="cp1251"
    )
    conn = read_ula_ini(ini)
    assert conn == UlaConnectionDetails("127.0.0.1", 1521, "TEST", "fictional")
    for bad in ("", "oracle server", " "):
        try:
            UlaConnectionDetails(bad, 1521, "TEST", "fictional").validated()
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid host accepted")

app = QApplication.instance() or QApplication([])
requested = []

def fake_factory(details, password, *, thick_client_dir=""):
    requested.append((details, password, thick_client_dir))
    return object()

page = UlaServicesTab(gateway_factory=fake_factory)
page.host.setText("127.0.0.1")
page.sid.setText("TEST")
page.username.setText("fictional")
page.password.setText("not-a-real-password")
gateway = page._make_gateway()
assert gateway is not None and len(requested) == 1
assert requested[0][0].username == "fictional"
assert page.password.echoMode() == page.password.EchoMode.Password
assert page.findChild(QTableWidget, "UlaServicesTable") is page.services
assert page.layout().itemAt(1).widget() is page.message, (
    "Oracle diagnostics must be visible before the long forms, not below tables"
)
assert "ORA-01017" in _safe_error(Exception("ORA-01017: private-credential"))
assert "private-credential" not in _safe_error(Exception("ORA-01017: private-credential"))
assert "DPY-3010" in _safe_error(Exception("DPY-3010: unsupported server"))
assert "unsupported server" not in _safe_error(Exception("DPY-3010: unsupported server"))
page._finished(
    page._generation,
    "find",
    (UlaClientHit("123", "Тестовый клиент", "ЮЛ"),),
    None,
)
assert page.hits_table.item(0, 0).text() == "123"
page.hits_table.selectRow(0)
app.processEvents()
assert page.client_id.text() == "123"
page._finished(
    page._generation, "services",
    (UlaService("50", "Видеоархив", "ЮЛ 30 дней", "1500.25", "10", "Активен"),),
    None,
)
assert page.services.rowCount() == 1
assert page.services.item(0, 1).text() == "ЮЛ 30 дней"
assert page.services.item(0, 2).text() == "1500.25"
assert page.services.item(0, 3).text() == "10"
page.close()
app.processEvents()

source = (Path(__file__).resolve().parents[1] / "linkvideo_vpn_helper/ui/pages/tariffs_page.py").read_text(encoding="utf-8")
assert 'self.workspace_tabs.addTab(self.ula_services_tab, "Россия · Ula (тест)")' in source

print("CORE TESTS 3.0.16 ULA QT UI AND CONFIG OK")
