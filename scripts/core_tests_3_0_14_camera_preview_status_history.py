from __future__ import annotations

"""Camera status/history/preview API contract regression; no real requests."""

import os
from datetime import date
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QDialog, QTableWidget

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from linkvideo_vpn_helper.services.b2o_camera_preview import (
    _safe_preview_url, preview_request_url,
)
from linkvideo_vpn_helper.services.billing_gateway import (
    BillingClient, BillingService, BillingValidationError, BillingPermissionDenied,
)
from linkvideo_vpn_helper.services.billing_token_transport import (
    BillingTokenTransport, BillingWriteOutcomeUnknown,
)
from linkvideo_vpn_helper.services.billing_editable_gateway import BillingEditableGateway
from linkvideo_vpn_helper.services.billing_response_parser import parse_cameras_response
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage

assert preview_request_url("Россия", "280535").endswith(
    "/operators/241/cameras/280535/preview_path"
)
assert preview_request_url("Беларусь", "280535").endswith(
    "/operators/1741/cameras/280535/preview_path"
)
assert preview_request_url("Казахстан", "299341").endswith(
    "/operators/1721/cameras/299341/preview_path"
)
assert _safe_preview_url(
    "https://b2o-vcore122.video.goodline.info:444/b2o-vcore122/test.jpeg"
).startswith("https://")
for url in (
    "http://b2o-vcore122.video.goodline.info/x",
    "https://evil.example.com/x",
    "https://b2o-vcore122.video.goodline.info:9997/x",
    "https://b2o-vcore122.video.goodline.info@evil.com/x",
):
    try:
        _safe_preview_url(url)
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe preview URL accepted: "+url)

camera = parse_cameras_response({
    "success": True,
    "response": [{"id_camera": "44", "id_camera_vc": "280535",
                  "short_name": "Камера 1", "status": "0"}],
})[0]
assert camera.id == "44" and camera.camera_vc_id == "280535"
assert camera.status == "Неактивный"
client = BillingClient(
    id="333", login="fictional_account", country="Беларусь",
    status="Активный", services=(camera,), services_loaded=True,
)

class Response:
    status_code = 200
    def __init__(self, body):
        self.body = body
    def json(self):
        return self.body

class Session:
    def __init__(self):
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/history"):
            return Response({"success": True, "response": [{
                "id_camera": "44", "date": "2026-10-28T19:47:15+07:00",
                "user": "TOOL_VC", "action": "Изменен статус",
                "field_name": "Статус", "old_value": "",
                "new_value": "Выключен",
            }]})
        return Response({"success": True, "response": [
            {"id_camera": "44", "id_camera_vc": "280535",
             "short_name": "Камера 1", "status": "0"}
        ]})
    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        return Response({"success": True, "response": 1})
    def close(self):
        pass

session = Session()
transport = BillingTokenTransport("fictional-token", header_mode="token", _session=session)
gateway = BillingEditableGateway(transport)
events = gateway.camera_history(client, "44")
assert len(events) == 1 and events[0]["new_value"] == "Выключен"
assert session.calls[0][1].endswith("/api/cameras/44/history")
assert session.calls[0][2]["allow_redirects"] is False

actual, updated = gateway.set_camera_status(client, "44", 0, date.today())
assert actual == {"success": True, "response": 1}
writes = [call for call in session.calls if call[0] == "PUT"]
assert len(writes) == 1
assert writes[0][1] == "https://api-billing-global.linkvideo.ru/api/cameras/44/status"
assert writes[0][2]["json"] == {
    "status": 0, "date": date.today().strftime("%d-%m-%Y")
}
assert writes[0][2]["allow_redirects"] is False
assert updated.id == "333"

for bad in ("45", "../44", "0"):
    before = len(session.calls)
    for operation in (
        lambda: gateway.camera_history(client, bad),
        lambda: gateway.set_camera_status(client, bad, 0, date.today()),
    ):
        try:
            operation()
        except (BillingValidationError, BillingPermissionDenied):
            pass
        else:
            raise AssertionError("Wrong-camera access accepted")
    assert len(session.calls) == before

app = QApplication.instance() or QApplication([])
page = TariffsPage(gateway=gateway)
page.client = client
page._render_client(client)
assert page.billing_dialog.property("helperModal") is True
assert len(page._preview_widgets) == 1
assert "44" in page._preview_widgets
page._on_camera_preview(page._preview_generation, "333", "44", None)
assert page._preview_widgets["44"].text().replace("\\n", "").strip()
page._on_camera_preview(page._preview_generation + 1, "333", "44", b"bad")
assert page._preview_widgets["44"].text()
with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
    page._show_camera_history(camera, events)
history = page.findChild(QTableWidget, "BillingCameraHistoryTable")
assert history is not None and history.rowCount() == 1
assert history.item(0, 5).text() == "Выключен"
assert history.parentWidget().property("helperModal") is True
with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Rejected):
    page._change_camera_status(camera)
    page._transfer_camera_preview(camera)
for name in ("BillingCameraStatusDialog", "BillingTransferCameraDialog"):
    modal = page.findChild(QDialog, name)
    assert modal is not None and modal.property("helperModal") is True, name
page.close()
app.processEvents()
print("CORE TESTS BILLING STATUS HISTORY AND B2O CAMERA PREVIEW CONTRACT OK")
