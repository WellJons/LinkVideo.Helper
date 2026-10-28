from __future__ import annotations

"""UI integration: login activates CRM GETs, logout clears prior customer data.

Uses only fake token and fake JSON transport. Never contacts corporate servers.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.billing_login_probe import LoginProbeResult
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeSession:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.routes = {
            "/api/accounts/search/lkz_demo_001": {
                "success": True, "response": [{
                    "id_account": "456", "login": "lkz_demo_001",
                    "account_name": "Demo account",
                    "country_name": "Казахстан",
                    "account_type_name": "Физическое лицо",
                    "services_status": "1/1", "status_name": "Активный",
                }]
            },
            "/api/accounts/456": {
                "success": True, "response": [{
                    "login": "lkz_demo_001", "account_name": "Demo account",
                    "balance": 0, "status_name": "Активный",
                }]
            },
            "/api/accounts/456/cameras": {
                "success": True, "response": [{
                    "id_camera": "88", "id_camera_vc": "999",
                    "short_name": "Camera demo", "tariff_name": "Trial",
                    "ab_plata": "0", "discount": "0",
                    "costs": "0", "status": "1",
                }]
            },
            "/api/tariffs/list/0": {
                "success": True, "response": [{
                    "id_tariff": 22, "tariff_name": "Demo tariff",
                    "country": "Казахстан", "account_type_name": "Физическое лицо",
                    "full_ab_plata": 500,
                }]
            },
            "/api/countries/1": {
                "success": True, "response": [{
                    "id_country": "3", "code": "KZ",
                    "name": "Казахстан", "currency_code": "тг.",
                    "prefix_login": "lkz",
                }]
            },
        }

    def get(self, url, **kwargs):
        prefix = "https://api-billing-global.linkvideo.ru"
        assert url.startswith(prefix)
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["token"] == "fictional-session-token"
        self.calls.append(url[len(prefix):])
        return FakeResponse(self.routes[url[len(prefix):]])

    def close(self):
        self.closed = True


app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as folder:
    settings = QSettings(str(Path(folder) / "helper.ini"), QSettings.Format.IniFormat)
    page = TariffsPage(settings=settings)
    assert not page.gateway.configured
    assert not page.search_btn.isEnabled()
    assert not page.all_tariffs_page.refresh.isEnabled()
    page.billing_login.username.setText("demo.employee")
    page.billing_login.password.setText("made-up-password")
    page.billing_login.remember.setChecked(False)
    outcome = LoginProbeResult(
        status_code=200, has_cookie=False,
        response_keys=("token",), has_token_field=True,
        token="fictional-session-token",
    )
    page.billing_login._on_login_done(0, outcome, None)
    assert page.billing_login.login_verified
    assert page.gateway.configured
    assert page.search_btn.isEnabled()
    assert page.all_tariffs_page.refresh.isEnabled()
    assert page.api_status.text() == "Авторизован"
    transport = page._owned_transport
    assert transport is not None
    assert transport.header_mode == "auto"

    # Strictly fake backend (no network).
    session = FakeSession()
    transport._session.close()
    transport._session = session
    client = page.gateway.find_client("lkz_demo_001")
    assert client is not None and client.services_loaded
    assert client.country == "Казахстан" and len(client.services) == 1
    assert transport.header_mode == "token"
    page.client = client
    page._render_client(client)
    assert page.services_table.rowCount() == 1
    assert page.metric_values["services"].text() == "1/1"

    catalog = page.gateway.all_tariffs()
    page.all_tariffs_page._on_result(page.all_tariffs_page._query, catalog, None)
    assert page.all_tariffs_page.table.rowCount() == 1
    assert page.all_tariffs_page.table.item(0, 4).text() == "500 тг."

    page.billing_login._logout()
    assert not page.gateway.configured
    assert page.client is None
    assert page.result_host.isHidden()
    assert page.services_table.rowCount() == 0
    assert page.all_tariffs_page.table.rowCount() == 0
    assert not page.search_btn.isEnabled()
    assert not page.all_tariffs_page.refresh.isEnabled()
    assert transport.token == ""
    assert not settings.contains("billing/crm/token")
    page.close()
app.processEvents()
print("CORE TESTS BILLING TOKEN UI CONNECT AND LOGOUT OK")
