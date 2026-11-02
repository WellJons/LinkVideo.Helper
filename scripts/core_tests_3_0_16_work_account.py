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

    # The sidebar exposes one billing connection for both endpoint sessions.
    # Exercise the presentation and retry logic without opening a real window.
    from linkvideo_vpn_helper.ui.main_window import MainWindow

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
        def setEnabled(self, value):
            self.enabled = bool(value)

    class FakeCRMPage:
        def __init__(self):
            self.billing_login = SimpleNamespace(_busy=False)
            self.calls = 0
        def _connect_work_account(self):
            self.calls += 1

    fake_crm_page = FakeCRMPage()
    retried = []
    fake_work_account = SimpleNamespace(
        b2o_busy=False,
        ensure_b2o=lambda *, force=False: retried.append(force),
    )
    fake_window = SimpleNamespace(
        _work_b2o_ok=None,
        _work_crm_ok=None,
        work_billing=FakeLabel(),
        work_reconnect_billing=FakeButton(),
        work_account=fake_work_account,
        _page_cache={"tariffs": fake_crm_page},
        _ensure_page=lambda key: fake_crm_page,
    )
    fake_window._refresh_work_billing_status = lambda: (
        MainWindow._refresh_work_billing_status(fake_window)
    )
    MainWindow._reconnect_work_billing(fake_window)
    assert retried == [True] and fake_crm_page.calls == 1
    assert "подключение" in fake_window.work_billing.value
    MainWindow._on_work_b2o_status(fake_window, True, "")
    assert "частично" in fake_window.work_billing.value
    MainWindow._on_work_crm_status(fake_window, True)
    assert fake_window.work_billing.value == "✓ Биллинг · подключён"
    assert "B2O" in fake_window.work_billing.hint
    assert "CRM" in fake_window.work_billing.hint
    MainWindow._on_work_crm_status(fake_window, False)
    assert "часть функций недоступна" in fake_window.work_billing.value
    fake_crm_page.billing_login._busy = True
    MainWindow._refresh_work_billing_status(fake_window)
    assert fake_window.work_reconnect_billing.enabled is False
    fake_crm_page.billing_login._busy = False
    fake_work_account.b2o_busy = True
    MainWindow._refresh_work_billing_status(fake_window)
    assert fake_window.work_reconnect_billing.enabled is False

    # Both archive pages must silently use the same work account and must
    # never open their own B2O password dialog in the main Helper workflow.
    from linkvideo_vpn_helper.services.archive_service import ArchiveService
    from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage
    from linkvideo_vpn_helper.ui.pages.archive_diagnostics_page import ArchiveDiagnosticsPage
    archive_service = ArchiveService(settings)
    download_page = ArchiveDownloadPage(
        archive_service, settings, work_account=account,
    )
    diagnostics_page = ArchiveDiagnosticsPage(
        archive_service, settings, work_account=account,
    )
    with patch("linkvideo_vpn_helper.ui.pages.archive_download_page.B2OLoginDialog",
               side_effect=AssertionError("Duplicate B2O dialog opened")):
        download_page._authorize()
    pump_until(lambda: download_page.btn_auth.isEnabled())
    assert download_page.auth_pill.text() == "B2O подключён"
    with patch("linkvideo_vpn_helper.ui.pages.archive_diagnostics_page.B2OLoginDialog",
               side_effect=AssertionError("Duplicate B2O dialog opened")):
        diagnostics_page._auth()
    pump_until(lambda: diagnostics_page.auth_btn.isEnabled())
    assert diagnostics_page.auth_pill.text() == "B2O подключён"
    download_page.close()
    diagnostics_page.close()

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
