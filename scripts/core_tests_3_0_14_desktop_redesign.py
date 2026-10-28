from __future__ import annotations

"""Regression: the desktop redesign must keep usable widgets and original flows."""

import ast
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "linkvideo_vpn_helper/theme.py",
    "linkvideo_vpn_helper/ui/main_window.py",
    "linkvideo_vpn_helper/ui/nav_icons.py",
    "linkvideo_vpn_helper/ui/login_window.py",
    "linkvideo_vpn_helper/ui/pages/settings_page.py",
    "linkvideo_vpn_helper/ui/pages/create_client_page.py",
    "linkvideo_vpn_helper/ui/pages/search_manage_page.py",
    "linkvideo_vpn_helper/ui/pages/archive_download_page.py",
    "linkvideo_vpn_helper/ui/pages/archive_diagnostics_page.py",
    "linkvideo_vpn_helper/ui/pages/inactive_clients_page.py",
    "linkvideo_vpn_helper/ui/pages/vpn_servers_page.py",
)
sources = {path: (ROOT / path).read_text(encoding="utf-8") for path in FILES}
for filename, source in sources.items():
    ast.parse(source, filename=filename)

login = sources["linkvideo_vpn_helper/ui/login_window.py"]
settings = sources["linkvideo_vpn_helper/ui/pages/settings_page.py"]
create = sources["linkvideo_vpn_helper/ui/pages/create_client_page.py"]
search = sources["linkvideo_vpn_helper/ui/pages/search_manage_page.py"]
theme = sources["linkvideo_vpn_helper/theme.py"]
main = sources["linkvideo_vpn_helper/ui/main_window.py"]

assert "class LoginIllustration(QWidget)" in login
assert 'form.setObjectName("LoginForm")' in login
assert 'self.error_banner.setObjectName("LoginError")' in login
assert "self._verification_signals.finished.connect(self._on_verified)" in login
assert "verify_routeros_user(user, password, hosts)" in login
assert 'self._shake_on_invalid_password()' in login

assert 'self.settings_tabs = QTabWidget()' in settings
for name in ("Внешний вид", "Архив", "VPN-серверы", "О программе"):
    assert f'"{name}"' in settings, name
assert "self.settings_tabs.addTab(" in settings
assert '"Облачный сервер"' in settings
assert "self._render_servers()" in settings
assert "self.server_rows = QVBoxLayout()" in settings
assert "self.animations_enabled" in settings

assert 'server_step.setObjectName("StepNumber")' in create
assert 'login_step.setObjectName("StepNumber")' in create
assert 'self.empty_result.setObjectName("CreateResultEmpty")' in create
assert "self.empty_result.hide()" in create
assert "self.result_text.show()" in create
assert "create_clients_batch(" in create

assert 'self.page_layout.addWidget(self.search_form)' in search
assert 'self.search_form.hide()' not in search
assert 'password_view.setEchoMode(QLineEdit.EchoMode.Password)' in search
assert "self._run_action(" in search
assert "self._render_client()" in search
assert "self._sync_selected_port_controls()" in search

assert 'user_card.setObjectName("SidebarUserCard")' in main
assert "navigation_icon(" in main
assert "def _refresh_nav_icons(self):" in main
assert "QSvgRenderer(QByteArray" in sources["linkvideo_vpn_helper/ui/nav_icons.py"]
assert "QFrame#Sidebar QPushButton[nav=" in theme
assert "QFrame#LoginForm" in theme
for filename in FILES:
    if filename.endswith((
        "archive_download_page.py", "archive_diagnostics_page.py",
        "inactive_clients_page.py", "vpn_servers_page.py",
    )):
        assert "PageHeader(" in sources[filename], filename
for filename in ("archive_download_page.py", "archive_diagnostics_page.py"):
    assert "WorkflowStep(" in sources["linkvideo_vpn_helper/ui/pages/" + filename]
assert "self.legend_toggle" in sources["linkvideo_vpn_helper/ui/pages/vpn_servers_page.py"]
assert "self.sort_row" in sources["linkvideo_vpn_helper/ui/pages/inactive_clients_page.py"]
assert "QTabWidget#SettingsTabs" in theme

# Build the controls offscreen where Qt's platform plugin is available.
# This catches signal/layout constructor regressions, without opening an
# external connection or modifying the operator's actual credentials.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QTabWidget

from linkvideo_vpn_helper.services.archive_service import ArchiveService
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService
from linkvideo_vpn_helper.ui.login_window import LoginWindow
from linkvideo_vpn_helper.ui.nav_icons import navigation_icon
from linkvideo_vpn_helper.ui.pages.create_client_page import CreateClientPage
from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage
from linkvideo_vpn_helper.ui.cloud_settings_integration import install_cloud_settings_ui
from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage
from linkvideo_vpn_helper.ui.pages.archive_diagnostics_page import ArchiveDiagnosticsPage
from linkvideo_vpn_helper.ui.pages.inactive_clients_page import InactiveClientsPage
from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as temp:
    cfg = QSettings(str(Path(temp) / "visual-smoke.ini"), QSettings.Format.IniFormat)
    cfg.setValue("ui/theme_v2", "linkvideo_2026")
    registry = ServerRegistry(cfg)
    service = VPNService()
    creds = SessionCredentials("visual-smoke", "test-only")
    search_service = FastSearchService(service)

    assert not navigation_icon("create").isNull()
    assert not navigation_icon("settings").isNull()
    login_window = LoginWindow(cfg)
    assert login_window.findChild(type(login_window.error_banner), "LoginForm") is not None
    login_window._status("Неверный логин или пароль", danger=True)
    assert not login_window.error_banner.isHidden()
    login_window._status("")
    assert login_window.error_banner.isHidden()

    install_cloud_settings_ui()
    settings_page = SettingsPage(cfg, registry, service, creds)
    assert isinstance(settings_page.settings_tabs, QTabWidget)
    assert settings_page.settings_tabs.count() == 5
    assert settings_page.appearance_card is not None
    assert settings_page.servers_card is not None
    assert settings_page.cloud_card is not None
    assert settings_page.cloud_layout.indexOf(settings_page.cloud_card) >= 0
    assert settings_page.cloud_save_button is not None
    assert settings_page.cloud_test_button is not None

    create_page = CreateClientPage(service, search_service, creds, registry)
    assert create_page.empty_result is not None
    assert create_page.result_text.isHidden()

    search_page = SearchManagePage(service, search_service, creds, registry)
    assert search_page.search_form is not None
    assert search_page.query is not None
    assert search_page.detail_panel is not None

    archive_service = ArchiveService(cfg)
    archive_page = ArchiveDownloadPage(archive_service, cfg)
    diagnostics_page = ArchiveDiagnosticsPage(archive_service, cfg)
    inactive_page = InactiveClientsPage(service, creds, registry)
    vpn_page = VPNServersPage(service, creds, registry)
    assert archive_page.start is not None and archive_page.btn_find is not None
    assert diagnostics_page.run is not None and diagnostics_page.report is not None
    assert inactive_page.refresh_btn is not None and inactive_page.sort_row is not None
    assert vpn_page.table is not None and vpn_page.legend_toggle is not None
    assert not vpn_page.legend_card.isVisible()

    for widget in (login_window, settings_page, create_page, search_page,
                   archive_page, diagnostics_page, inactive_page, vpn_page):
        widget.close()
    app.processEvents()

print("CORE TESTS DESKTOP REDESIGN UI SMOKE OK")
