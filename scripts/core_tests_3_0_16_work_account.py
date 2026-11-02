from __future__ import annotations
"""Single-work-account regressions; all external requests are mocked."""
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
from linkvideo_vpn_helper.services.work_account_auth import WorkAccountAuth
from linkvideo_vpn_helper.services.billing_credential_store import BillingCredentialStore
from linkvideo_vpn_helper.ui.billing_login_panel import BillingLoginPanel
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage

app = QApplication.instance() or QApplication([])

def pump_until(predicate):
    for _ in range(280):
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out on synthetic work-account login")

with tempfile.TemporaryDirectory() as temporary:
    settings = QSettings(str(Path(temporary) / "work.ini"), QSettings.Format.IniFormat)
    settings.setValue("b2o/token", "old-user-token")
    settings.setValue("b2o/login", "old-user")
    settings.setValue("billing/crm/username", "old-user")
    settings.setValue("billing/crm/password_dpapi", "dpapi:old-secret")
    credentials = SessionCredentials("shared.employee", "not-a-real-secret")
    seen = []

    class FakeB2O:
        def __init__(self, setting):
            self.setting = setting
        def login(self, login, password):
            seen.append(("b2o", login, password))
            assert login == "shared.employee" and password == "not-a-real-secret"
            self.setting.setValue("b2o/login", login)
            self.setting.setValue("b2o/token", "independent-b2o-token")
            return "independent-b2o-token"

    account = WorkAccountAuth(credentials, settings, b2o_factory=FakeB2O)
    account.b2oChanged.connect(account.on_b2o_result)
    assert not settings.value("b2o/token", "", str)
    assert not settings.value("b2o/login", "", str)
    assert not settings.contains("billing/crm/password_dpapi")
    assert account.username == "shared.employee"
    results = []
    account.b2oChanged.connect(lambda ok, err: results.append((ok, err)))
    assert account.ensure_b2o() is False
    pump_until(lambda: len(results) >= 1)
    assert results[-1] == (True, "")
    assert settings.value("b2o/token", "", str) == "independent-b2o-token"
    assert account.ensure_b2o() is True  # Already authenticated; no extra login.
    assert len(seen) == 1

    checked = []
    def crm_checker(request):
        checked.append((request.login, request.password))
        return SimpleNamespace(token="independent-crm-token")
    panel = BillingLoginPanel(settings, login_checker=crm_checker)
    changed = []
    panel.sessionChanged.connect(changed.append)
    assert panel.login_with_work_account(credentials)
    pump_until(lambda: panel.login_verified)
    assert changed[-1] is True
    assert panel.session_token == "independent-crm-token"
    assert panel.connected_account.text() == "shared.employee"
    assert panel.password.text() == ""  # Never copy work password into UI.
    assert not settings.contains("billing/crm/password_dpapi")
    assert checked == [("shared.employee", "not-a-real-secret")]
    assert "not-a-real-secret" not in repr(panel.session_token)

    panel._drop_session()
    def refused(_request):
        raise RuntimeError("server leaked secret not-a-real-secret")
    panel.checker = refused
    refused_messages = []
    panel.loginFailed.connect(refused_messages.append)
    assert panel.login_with_work_account(credentials)
    pump_until(lambda: bool(refused_messages))
    assert not panel.login_verified
    assert "not-a-real-secret" not in refused_messages[-1]
    panel.close()

    with patch("linkvideo_vpn_helper.ui.billing_login_panel.check_billing_login", side_effect=crm_checker):
        page = TariffsPage(settings=settings, work_account=account)
        page.billing_login.checker = crm_checker
        pump_until(lambda: page.billing_login.login_verified)
        assert page.gateway.configured
        assert page.auth_btn.text() == "Переподключить CRM"
        assert not page.billing_dialog.isVisible()
        assert not settings.contains("billing/crm/password_dpapi")
        page.close()

    class RejectB2O(FakeB2O):
        def login(self, *args):
            raise RuntimeError("private secret: not-a-real-secret")
    rejected = WorkAccountAuth(credentials, settings, b2o_factory=RejectB2O)
    rejected.b2oChanged.connect(rejected.on_b2o_result)
    fails = []
    rejected.b2oChanged.connect(lambda ok, error: fails.append((ok, error)))
    assert rejected.ensure_b2o() is False
    pump_until(lambda: bool(fails))
    assert fails[0][0] is False
    assert "not-a-real-secret" not in fails[0][1]
    assert not settings.value("b2o/token", "", str)
    rejected.clear()

print("CORE TESTS 3.0.16 SINGLE WORK ACCOUNT FOR B2O AND CRM OK")
