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
    "linkvideo_vpn_helper/ui/login_window.py",
    "linkvideo_vpn_helper/ui/pages/settings_page.py",
    "linkvideo_vpn_helper/ui/pages/create_client_page.py",
    "linkvideo_vpn_helper/ui/pages/search_manage_page.py",
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
assert "QFrame#Sidebar QPushButton[nav=" in theme
assert "QFrame#LoginForm" in theme
assert "QTabWidget#SettingsTabs" in theme

# Build the controls offscreen where Qt's platform plugin is available.
# This catches signal/layout constructor regressions, without opening an
# external connection or modifying the operator's actual credentials.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QTabWidget

from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService
from linkvideo_vpn_helper.ui.login_window import LoginWindow
from linkvideo_vpn_helper.ui.pages.create_client_page import CreateClientPage
from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as temp:
    cfg = QSettings(str(Path(temp) / "visual-smoke.ini"), QSettings.Format.IniFormat)
    cfg.setValue("ui/theme_v2", "linkvideo_2026")
    registry = ServerRegistry(cfg)
    service = VPNService()
    creds = SessionCredentials("visual-smoke", "test-only")
    search_service = FastSearchService(service)

    login_window = LoginWindow(cfg)
    assert login_window.findChild(type(login_window.error_banner), "LoginForm") is not None
    login_window._status("Неверный логин или пароль", danger=True)
    assert not login_window.error_banner.isHidden()
    login_window._status("")
    assert login_window.error_banner.isHidden()

    settings_page = SettingsPage(cfg, registry, service, creds)
    assert isinstance(settings_page.settings_tabs, QTabWidget)
    assert settings_page.settings_tabs.count() == 5
    assert settings_page.appearance_card is not None
    assert settings_page.servers_card is not None

    create_page = CreateClientPage(service, search_service, creds, registry)
    assert create_page.empty_result is not None
    assert create_page.result_text.isHidden()

    search_page = SearchManagePage(service, search_service, creds, registry)
    assert search_page.search_form is not None
    assert search_page.query is not None
    assert search_page.detail_panel is not None

    for widget in (login_window, settings_page, create_page, search_page):
        widget.close()
    app.processEvents()

print("CORE TESTS DESKTOP REDESIGN UI SMOKE OK")
