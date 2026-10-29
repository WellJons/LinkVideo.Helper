from __future__ import annotations

"""CRM autologin, history date/card rendering, and startup process guard.

All values are fake and no external network is contacted.
"""

import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QPushButton, QScrollArea

from linkvideo_vpn_helper.services.billing_credential_store import BillingCredentialStore
from linkvideo_vpn_helper.services.billing_gateway import BillingClient, BillingService
from linkvideo_vpn_helper.ui.billing_login_panel import BillingLoginPanel
from linkvideo_vpn_helper.ui.pages.tariffs_page import (
    TariffsPage, format_history_timestamp, admin_camera_label,
)
from linkvideo_vpn_helper.ui import billing_login_panel as login_module

assert format_history_timestamp("2026-07-07T00:01:38+07:00") == "07.07.2026 00:01:38"
assert format_history_timestamp("2026-10-28T19:47:15+07:00") == "28.10.2026 19:47:15"
assert format_history_timestamp("2026-07-07T00:01:38Z") == "07.07.2026 00:01:38"
assert format_history_timestamp("") == "—"
assert admin_camera_label("Беларусь", "280535") == "linkvideoby_280535"
assert admin_camera_label("Казахстан", "299341") == "299341"

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    settings = QSettings(str(Path(tmp) / "prefs.ini"), QSettings.Format.IniFormat)
    store = BillingCredentialStore(
        settings, platform="win32",
        protector=lambda s: "dpapi:" + s[::-1],
        unprotector=lambda s: s[6:][::-1],
    )
    seen = []
    def checker(request):
        seen.append((request.login, request.password))
        return SimpleNamespace(token="fictional-ram-only-token")

    panel = BillingLoginPanel(settings, login_checker=checker, credential_store=store)
    assert not panel.auto_login_remembered()
    panel.close()
    panel.deleteLater()
    app.processEvents()
    store.save("demo.crm", "fakepass", remember=True)
    panel = BillingLoginPanel(settings, login_checker=checker, credential_store=store)
    assert panel.auto_login_remembered()
    for _ in range(100):
        app.processEvents()
        if panel.login_verified:
            break
        time.sleep(0.01)
    assert panel.login_verified and seen == [("demo.crm", "fakepass")]
    assert not settings.contains("billing/crm/token")
    assert "fictional-ram-only-token" not in Path(tmp, "prefs.ini").read_text()
    assert not panel.auto_login_remembered()
    panel.close()
    panel.deleteLater()
    app.processEvents()

    class FakeGateway:
        configured = True
        can_change_tariffs = False

    page = TariffsPage(gateway=FakeGateway(), settings=settings)
    svc = BillingService(
        id="44", camera_id="44", camera_vc_id="280535",
        camera_name="Камера 1", tariff_name="ЮЛ 30 дней",
        price_text="0", discount_text="0", total_text="0",
        status="Активный",
    )
    client = BillingClient(
        id="333", login="example", country="Беларусь",
        status="Активный", services=(svc,), services_loaded=True,
    )
    page.client = client
    page._render_client(client)
    thumbs = page.findChildren(QLabel, "BillingCameraPreview")
    assert thumbs and thumbs[0].width() >= 100
    assert any("ID админки: linkvideoby_280535" in x.text() for x in page.findChildren(QLabel))
    actions = page.findChild(QPushButton, "BillingCameraOtherActions")
    assert actions.text() == "Действия" and len(actions.menu().actions()) == 4

    sample = [{
        "date": "2026-07-07T00:01:38+07:00", "user": "TOOL_VC",
        "action": "Изменен тариф", "field_name": "Тарифный план",
        "old_value": "Стартовый 90", "new_value": "ЮЛ 30 дней",
    }]
    with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
        page._show_camera_history(svc, sample)
    dialog = page.findChild(QDialog, "BillingCameraHistoryDialog")
    assert dialog is not None
    assert dialog.findChild(QScrollArea, "BillingHistoryScroll") is not None
    assert len(dialog.findChildren(QFrame, "BillingHistoryEvent")) == 1
    texts = [x.text() for x in dialog.findChildren(QLabel)]
    assert "07.07.2026 00:01:38" in texts
    assert any("Стартовый 90" in x and "ЮЛ 30 дней" in x for x in texts)
    assert not any("+07:00" in x for x in texts)
    dialog = None
    page.close()
    page.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

source = (Path(__file__).resolve().parents[1] /
          "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
assert source.index("_install_no_console_process_guard()") < source.index("login = LoginWindow(settings)")
print("CORE TESTS CRM AUTOLOGIN, HISTORY AND NO-CONSOLE EARLY GUARD OK")
