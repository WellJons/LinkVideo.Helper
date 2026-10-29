from __future__ import annotations

"""Observed CRM comment payloads and responsive VPN workspace, no real HTTP."""
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QListWidget, QWidget
from linkvideo_vpn_helper.services.billing_token_transport import BillingTokenTransport
from linkvideo_vpn_helper.services.billing_gateway import BillingClient, BillingService, BillingValidationError
from linkvideo_vpn_helper.services.billing_catalog import BillingCountry, TariffCatalogEntry, TariffReferenceCatalog
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffCatalogDialog, TariffsPage
from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from decimal import Decimal

class FakeResponse:
    status_code = 200
    def json(self):
        return {"success": True, "response": {"result": "9", "message": "Успешно", "remain_pay": 0}}

class FakeSession:
    def __init__(self):
        self.writes=[]
    def put(self, url, **kwargs):
        self.writes.append((url, kwargs))
        return FakeResponse()
    def close(self):
        pass

session = FakeSession()
transport = BillingTokenTransport("fake-token", header_mode="token", _session=session)
day = date.today() + timedelta(days=1)
assert transport.put_tariff("304","230",day,comment="  Причина смены  ")["success"]
assert session.writes[-1][0].endswith("/api/cameras/304/tariff/230")
assert session.writes[-1][1]["json"] == {
    "date":day.strftime("%d-%m-%Y"),"description":"Причина смены",
}
assert transport.put_camera_status("304",0,day,comment="  Обращение клиента  ")["success"]
assert session.writes[-1][0].endswith("/api/cameras/304/status")
assert session.writes[-1][1]["json"] == {
    "status":0, "date":day.strftime("%d-%m-%Y"),"comments":"Обращение клиента",
}
transport.put_tariff("304","230",day)
assert session.writes[-1][1]["json"] == {"date":day.strftime("%d-%m-%Y")}
transport.put_camera_status("304",1,day)
assert session.writes[-1][1]["json"] == {"status":1,"date":day.strftime("%d-%m-%Y")}
for comment in ("X"*501, None, 12):
    before = len(session.writes)
    for method in (lambda: transport.put_tariff("304","230",day,comment),
                   lambda: transport.put_camera_status("304",1,day,comment)):
        try:
            method()
        except BillingValidationError:
            pass
        else:
            raise AssertionError("Invalid comment accepted")
    assert len(session.writes)==before
transport.close()

app = QApplication.instance() or QApplication([])
camera = BillingService("304","Камера 1","304","ЮЛ 7 дней","0","0","0","Активный",camera_vc_id="299341")
client = BillingClient("333","fictional","Казахстан","Активный",services=(camera,),services_loaded=True)
catalog = TariffReferenceCatalog(
    tariffs=(TariffCatalogEntry("230","ЮЛ 7 дней","Казахстан","Физическое лицо",Decimal("0")),),
    archive_periods=(),country=BillingCountry("3","KZ","Казахстан","тг.","lkz"),
)
picker=TariffCatalogDialog(catalog,"Физическое лицо",service=camera)
picker.tariff_cards.setCurrentRow(0)
picker.comment_edit.setText("Комментарий тарифа")
assert picker.selection_change(client).comment == "Комментарий тарифа"
picker.close()
app.processEvents()

with tempfile.TemporaryDirectory() as folder:
    registry = ServerRegistry(QSettings(str(Path(folder)/"prefs.ini"),QSettings.Format.IniFormat))
    page=SearchManagePage(
        service=object(), search=object(),
        credentials=SessionCredentials("fictional","none"),
        registry=registry,
    )
    page.current=ClientRecord(
        server="vpn03.linkvideo.ru",login="fictional_01",
        password="fake",remote_address="172.16.2.183",
        ports=[11398,11399],is_online=True,active_ports=[11398,11399]
    )
    page._open_client_view()
    page.resize(1100,850)
    page.show()
    app.processEvents()
    assert page.client_info_card.minimumWidth() >= 400
    assert page.detail_panel.minimumWidth() <= 450
    assert not page.compact_search.isHidden()
    assert "fictional_01" in page.compact_search_label.text()
    assert page._search_form_anim is not None
    assert page.info_title.text() == "Клиент fictional_01"
    assert page.info_online.text()=="В сети"
    assert page.info_title.parentWidget() is not page.info_host
    assert page.info_scroll.property("lvWheelBoundaryLock")
    assert page.port_list.parentWidget() is not page.detail_host
    assert page.port_list.count()==2
    assert page.port_list.item(0).text().count("Порт 11398") == 1
    assert page.back_to_results.isHidden()
    assert page.detail_route_title.text() == "Управление аккаунтом"
    assert page.info_scroll.verticalScrollBar().value()==0
    assert page.port_list.height()<=275
    page.close()
    app.processEvents()

print("CORE TESTS BILLING COMMENTS & VPN RESPONSIVE CARD OK")
