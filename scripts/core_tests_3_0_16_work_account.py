from __future__ import annotations
"""Distinct MikroTik/admin authentication regressions, using only fake APIs."""
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
from linkvideo_vpn_helper.services.billing_credential_store import StoredBillingLogin
from linkvideo_vpn_helper.ui.billing_login_panel import BillingLoginPanel
from linkvideo_vpn_helper.ui.admin_login_dialog import AdminLoginDialog
from linkvideo_vpn_helper.ui.pages.tariffs_page import TariffsPage

app = QApplication.instance() or QApplication([])

def pump_until(predicate):
    for _ in range(350):
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out while checking admin credentials")

class FakeStore:
    def __init__(self):
        self.saved = StoredBillingLogin("", "")
        self.save_count = 0
    def load(self):
        return self.saved
    def save(self, username, password, *, remember):
        self.saved = StoredBillingLogin(username, password) if remember else StoredBillingLogin("", "")
        self.save_count += 1
    def clear(self):
        self.saved = StoredBillingLogin("", "")
    def b2o_login_name(self):
        return ""

class FakeLabel:
    def __init__(self):
        self.value = ""
        self.hint = ""
    def setText(self, value):
        self.value = value
    def setToolTip(self, value):
        self.hint = value

class FakeButton:
    def __init__(self):
        self.enabled = True
        self.value = ""
    def setEnabled(self, value):
        self.enabled = bool(value)
    def setText(self, value):
        self.value = value

with tempfile.TemporaryDirectory() as temporary:
    settings = QSettings(str(Path(temporary) / "work.ini"), QSettings.Format.IniFormat)
    settings.setValue("b2o/token", "previous-operator-token")
    router = SessionCredentials("router.employee", "router-secret")
    admin_store = FakeStore()
    seen = []

    class FakeB2O:
        def __init__(self, setting):
            self.setting = setting
        def login(self, username, password):
            seen.append((username, password))
            assert username == "admin.employee" and password == "admin-secret"
            assert password != router.password
            self.setting.setValue("b2o/login", username)
            self.setting.setValue("b2o/token", "admin-b2o-token")
            return "admin-b2o-token"

    account = WorkAccountAuth(router, settings, b2o_factory=FakeB2O,
                              credential_store=admin_store)
    account.b2oChanged.connect(account.on_b2o_result)
    assert not account.has_credentials
    assert not settings.value("b2o/token", "", str)
    assert account.username != router.username
    assert not account.ensure_b2o()
    assert not seen
    assert admin_store.save_count == 0

    account.set_admin_credentials("admin.employee", "admin-secret", remember=True)
    assert account.username == "admin.employee"
    assert admin_store.save_count == 0  # Unverified passwords are never saved.
    events = []
    account.b2oChanged.connect(lambda ok, error: events.append((ok, error)))
    assert account.ensure_b2o() is False
    pump_until(lambda: bool(events))
    assert events[-1] == (True, "")
    assert account.ensure_b2o() is True
    assert seen == [("admin.employee", "admin-secret")]
    assert admin_store.saved == StoredBillingLogin("admin.employee", "admin-secret")
    assert admin_store.save_count == 1
    assert settings.value("b2o/token", "", str) == "admin-b2o-token"

    checked = []
    def crm_checker(request):
        checked.append((request.login, request.password))
        return SimpleNamespace(token="different-crm-token")

    panel = BillingLoginPanel(settings, login_checker=crm_checker,
                              credential_store=admin_store)
    assert panel.login_with_work_account(account.credentials)
    pump_until(lambda: panel.login_verified)
    assert panel.session_token == "different-crm-token"
    assert checked == [("admin.employee", "admin-secret")]
    assert admin_store.save_count == 1
    assert admin_store.saved.password == "admin-secret"
    assert panel.password.text() == ""
    panel.close()

    # Remembered admin data are restored independently of the MikroTik login.
    saved_account = WorkAccountAuth(router, settings, b2o_factory=FakeB2O,
                                   credential_store=admin_store)
    saved_account.b2oChanged.connect(saved_account.on_b2o_result)
    assert saved_account.username == "admin.employee"
    assert not settings.value("b2o/token", "", str)
    refreshed = []
    saved_account.b2oChanged.connect(lambda ok, err: refreshed.append(ok))
    saved_account.ensure_b2o()
    pump_until(lambda: bool(refreshed))
    assert refreshed == [True]

    # Manual admin dialog has the familiar router login fields and remember.
    dialog = AdminLoginDialog("admin.employee")
    assert dialog.login.text() == "admin.employee"
    assert dialog.remember.isChecked()
    assert dialog.password.echoMode() == dialog.password.EchoMode.Password
    dialog.password.setText("admin-secret")
    assert dialog.credentials() == ("admin.employee", "admin-secret", True)
    dialog.close()

    # CRM is not sent the MikroTik credentials when opening Tariffs.
    with patch("linkvideo_vpn_helper.ui.billing_login_panel.check_billing_login",
               side_effect=crm_checker):
        page = TariffsPage(settings=settings, work_account=saved_account)
        page.billing_login.checker = crm_checker
        pump_until(lambda: page.billing_login.login_verified)
        assert page.gateway.configured
        assert not page.billing_dialog.isVisible()
        page.close()
    assert all(username == "admin.employee" for username, _ in checked)

    # Archive integration silently uses B2O session without a second password form.
    from linkvideo_vpn_helper.services.archive_service import ArchiveService
    from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage
    from linkvideo_vpn_helper.ui.pages.archive_diagnostics_page import ArchiveDiagnosticsPage
    archive = ArchiveService(settings)
    download_page = ArchiveDownloadPage(archive, settings, work_account=saved_account)
    diagnostics_page = ArchiveDiagnosticsPage(archive, settings, work_account=saved_account)
    with patch("linkvideo_vpn_helper.ui.pages.archive_download_page.B2OLoginDialog",
               side_effect=AssertionError("Duplicate admin form opened")):
        download_page._authorize()
    pump_until(lambda: download_page.btn_auth.isEnabled())
    assert download_page.auth_pill.text() == "B2O подключён"
    with patch("linkvideo_vpn_helper.ui.pages.archive_diagnostics_page.B2OLoginDialog",
               side_effect=AssertionError("Duplicate admin form opened")):
        diagnostics_page._auth()
    pump_until(lambda: diagnostics_page.auth_btn.isEnabled())
    download_page.close()
    diagnostics_page.close()

    # Only two side-by-side account cards; the UI does not ask for B2O/CRM
    # credentials independently or present them as user-facing accounts.
    from linkvideo_vpn_helper.ui.main_window import MainWindow
    class MockWindow:
        _work_b2o_ok = None
        _work_crm_ok = None
        work_account = saved_account
        admin_name = FakeLabel()
        admin_status = FakeLabel()
        admin_login_btn = FakeButton()
        admin_retry_btn = FakeButton()
        admin_forget_btn = FakeButton()
        _page_cache = {}
    mock_window = MockWindow()
    MainWindow._refresh_work_billing_status(mock_window)
    assert mock_window.admin_name.value == "admin.employee"
    assert "B2O" not in mock_window.admin_status.value
    assert "CRM" not in mock_window.admin_status.value
    mock_window._work_b2o_ok = True
    MainWindow._refresh_work_billing_status(mock_window)
    assert mock_window.admin_status.value == "Камеры подключены"
    mock_window._work_crm_ok = True
    MainWindow._refresh_work_billing_status(mock_window)
    assert mock_window.admin_status.value == "Подключена"
    mock_window._work_crm_ok = False
    MainWindow._refresh_work_billing_status(mock_window)
    assert mock_window.admin_status.value == "Часть функций недоступна"

    saved_account.clear(forget=True)
    assert not saved_account.has_credentials
    assert not admin_store.saved.username
    assert not settings.value("b2o/token", "", str)
    assert router.username == "router.employee"

print("CORE TESTS 3.0.16 DISTINCT ROUTEROS/ADMIN LOGINS OK")
