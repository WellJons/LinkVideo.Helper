from __future__ import annotations

"""Captured CRM owner PUT and user-facing action/wheel safeguards.

All IDs and credentials are fictional.  No external HTTP requests are made.
"""

import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QLabel, QListWidget, QPushButton,
)
from linkvideo_vpn_helper.services.billing_token_transport import (
    BillingTokenTransport, BillingWriteOutcomeUnknown,
)
from linkvideo_vpn_helper.services.billing_editable_gateway import BillingEditableGateway
from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingService, BillingNotConfigured, BillingPermissionDenied,
)
from linkvideo_vpn_helper.services.billing_search_parser import BillingSearchHit
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage, currency_for_client
from linkvideo_vpn_helper.ui.nested_scroll_guard import _NestedScrollFilter
from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials
from linkvideo_vpn_helper.services.server_registry import ServerRegistry

assert currency_for_client("Беларусь") == "BYN"
assert currency_for_client("Казахстан") == "₸"

class Response:
    status_code = 200
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        return self.payload

class Session:
    def __init__(self):
        self.calls = []
        self.put_result = {"success": True, "response": 0}
        self.transferred = False
    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/accounts/149"):
            return Response({
                "success": True,
                "response": [{"login": "lkz_fake_receiver", "status_name": "Неактивный"}],
            })
        if url.endswith("/accounts/149/cameras"):
            return Response({"success": True, "response": []})
        if url.endswith("/accounts/333/cameras"):
            rows = [] if self.transferred else [{
                "id_camera": "304", "id_camera_vc": "299341",
                "short_name": "Камера 1", "status": "0",
            }]
            return Response({"success": True, "response": rows})
        raise AssertionError("Unexpected GET: "+url)
    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        self.transferred = self.put_result.get("success") is True and self.put_result.get("response") == 0
        return Response(self.put_result)
    def close(self):
        pass

camera = BillingService(
    id="304", camera_id="304", camera_vc_id="299341",
    camera_name="Камера 1", tariff_name="ЮЛ 3 дня", price_text="0",
    discount_text="0", total_text="0", status="Неактивный",
)
client = BillingClient(
    id="333", login="lkz_fake_sender", country="Казахстан", status="Активный",
    services=(camera,), services_loaded=True,
)
recipient = BillingSearchHit(
    id="149", login="lkz_fake_receiver", account_name="Receiver",
    country="Казахстан", account_type="Физическое лицо",
    status="Неактивный", services_status="", balance_text="0",
)
session = Session()
transport = BillingTokenTransport(
    "fictional-token", header_mode="token", _session=session
)
gateway = BillingEditableGateway(transport)
result, updated = gateway.transfer_camera(client, "304", recipient, move_in_vc=False)
assert result == {"success": True, "response": 0}
assert updated is not None and updated.id == client.id
writes = [x for x in session.calls if x[0] == "PUT"]
assert len(writes) == 1
assert writes[0][1] == "https://api-billing-global.linkvideo.ru/api/cameras/304/owner"
assert writes[0][2]["json"] == {
    "from_id_account": "333", "to_id_account": "149", "move_in_vc": "0",
}
assert writes[0][2]["allow_redirects"] is False
assert session.calls.index(writes[0]) > 1  # recipient was verified first

for bad_recipient, bad_camera, move in (
    (recipient, "305", False),
    (recipient, "304", True),
    (BillingSearchHit(
        id="149", login=recipient.login, account_name="Receiver",
        country="Беларусь", account_type="Физическое лицо",
        status="", services_status="", balance_text="0",
    ), "304", False),
):
    before = len([x for x in session.calls if x[0] == "PUT"])
    try:
        gateway.transfer_camera(client, bad_camera, bad_recipient, move_in_vc=move)
    except (BillingNotConfigured, BillingPermissionDenied):
        pass
    else:
        raise AssertionError("Unsafe transfer unexpectedly allowed")
    assert len([x for x in session.calls if x[0] == "PUT"]) == before

session.transferred = False
session.put_result = {"success": True, "response": 1}
try:
    gateway.transfer_camera(client, "304", recipient)
except BillingWriteOutcomeUnknown:
    pass
else:
    raise AssertionError("Unexpected owner response must not be accepted")

app = QApplication.instance() or QApplication([])
page = TariffsPage(gateway=gateway)
page.client = client
page._render_client(client)
all_texts = [w.text() for w in page.findChildren(QLabel)]
assert any("Итого: 0 ₸" in text for text in all_texts), all_texts
thumb = page._preview_widgets["304"]
assert thumb.text() == "Нет превью"
page._on_camera_preview(page._preview_generation, "333", "304", None)
assert thumb.text() == "Нет превью"
with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Rejected):
    page._change_camera_status(camera)
    page._transfer_camera_preview(camera)
status_dialog = page.findChild(QDialog, "BillingCameraStatusDialog")
assert status_dialog is not None
assert status_dialog.property("helperModal") is True
assert status_dialog.width() >= 510
assert not any("\\n" in x.text() for x in status_dialog.findChildren(QLabel))
owner_dialog = page.findChild(QDialog, "BillingTransferCameraDialog")
assert owner_dialog is not None
assert owner_dialog.findChild(QPushButton, "BillingTransferSubmit").isEnabled() is False
assert owner_dialog.findChild(QCheckBox, "BillingTransferMoveInVC").isEnabled() is False
page.close()
app.processEvents()

with tempfile.TemporaryDirectory() as directory:
    settings = QSettings(str(Path(directory) / "app.ini"), QSettings.Format.IniFormat)
    vpn = SearchManagePage(
        service=object(), search=object(),
        credentials=SessionCredentials("fake", "fake"),
        registry=ServerRegistry(settings),
    )
    vpn.current = ClientRecord(
        server="vpn02.linkvideo.ru", login="fictional_01",
        password="fictional-only", remote_address="172.16.2.133",
        ports=[11690, 11691, 11692], is_online=False,
        active_ports=[11690, 11691],
    )
    vpn._open_client_view()
    app.processEvents()
    assert vpn.detail_scroll.property("lvWheelBoundaryLock") is True
    assert vpn.port_list.property("lvWheelBoundaryLock") is True
    filter_ = _NestedScrollFilter()
    vpn.page_scroll.verticalScrollBar().setRange(0, 200)
    vpn.page_scroll.verticalScrollBar().setValue(80)
    vpn.port_list.verticalScrollBar().setValue(0)
    wheel = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    assert filter_.eventFilter(vpn.port_list.viewport(), wheel) is True
    assert vpn.page_scroll.verticalScrollBar().value() == 80
    vpn.close()
    app.processEvents()

print("CORE TESTS OWNER TRANSFER, STATUS MODAL, CURRENCY, PREVIEW AND WHEEL LOCK OK")
