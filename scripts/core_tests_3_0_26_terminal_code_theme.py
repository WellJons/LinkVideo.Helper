from __future__ import annotations

"""Terminal Code theme regression: full Qt skin and safe live theme switching.

Pure visual checks. No RouterOS, CRM, VPN service, or network connections.
"""

import ast
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton

from linkvideo_vpn_helper import theme as palette
from linkvideo_vpn_helper.ui.login_window import LoginWindow
from linkvideo_vpn_helper.ui.visual_density import install_visual_density
from linkvideo_vpn_helper.brand_theme import install_linkvideo_brand_theme

ROOT = Path(__file__).resolve().parents[1]
THEME = "terminal_code"

assert palette.normalize_theme(THEME) == THEME
assert palette.theme_names()[0][0] == THEME
assert "Terminal // Code" in dict(palette.theme_names()).values()
for field in (
    "bg", "sidebar", "panel", "panel2", "border", "text", "muted",
    "accent", "accent_text", "accent_soft", "info", "danger",
    "success", "warning", "graph_rx",
):
    assert palette.colors(THEME)[field].startswith("#"), field

app = QApplication.instance() or QApplication([])
install_linkvideo_brand_theme()
install_visual_density()
terminal = palette.get_theme_style(THEME)
assert len(terminal) > 15000
for selector in (
    "QFrame#TerminalTopbar", "QFrame#Sidebar", "QFrame#Card",
    "QFrame#LoginForm", "QDialog[helperModal=", "QComboBox",
    "QLineEdit", "QPlainTextEdit", "QTableWidget", "QTableView",
    "QHeaderView::section", "QTabWidget#SettingsTabs",
    "QMenu", "QProgressBar", "QCalendarWidget#CompactCalendar",
    "QPushButton[nav=", "QPushButton[role=", "QLabel[pill=",
):
    assert selector in terminal, selector

assert "#56E2AC" in terminal and "Cascadia Code" in terminal
assert terminal.count("{") == terminal.count("}"), "Unbalanced QSS braces"
assert "QFrame#TerminalTopbar" not in palette.get_theme_style("ocean_blue")
assert "QFrame#TerminalTopbar" not in palette.get_theme_style("linkvideo_2026")

for source in (
    "linkvideo_vpn_helper/terminal_code_theme.py",
    "linkvideo_vpn_helper/theme.py",
    "linkvideo_vpn_helper/brand_theme.py",
    "linkvideo_vpn_helper/ui/main_window.py",
    "linkvideo_vpn_helper/ui/login_window.py",
    "linkvideo_vpn_helper/ui/visual_density.py",
    "linkvideo_vpn_helper/ui/pages/settings_page.py",
):
    ast.parse((ROOT / source).read_text(encoding="utf-8"), filename=source)

main_source = (ROOT / "linkvideo_vpn_helper/ui/main_window.py").read_text(encoding="utf-8")
assert "self.terminal_topbar = QFrame()" in main_source
assert 'self.terminal_topbar.setVisible(enabled)' in main_source
assert '"./" + self.TERMINAL_PATHS.get(' in main_source
assert "self._sync_terminal_chrome()" in main_source

with tempfile.TemporaryDirectory() as temp:
    settings = QSettings(str(Path(temp) / "config.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/theme_v2", THEME)
    settings.setValue("remember", False)
    settings.setValue("username", "")
    settings.setValue("password", "")

    login = LoginWindow(settings)
    login.show()
    app.processEvents()
    assert login.styleSheet().find("QFrame#TerminalTopbar") >= 0
    prompt = [
        item for item in login.findChildren(QLabel, "TerminalPrompt")
        if "AUTHENTICATE" in item.text()
    ]
    assert len(prompt) == 1 and prompt[0].isVisible()
    assert login.brand_panel.theme_key == THEME
    assert login.login.isEnabled() and login.password.isEnabled() and login.submit.isEnabled()
    login.close()

    settings.setValue("ui/theme_v2", "ocean_blue")
    normal_login = LoginWindow(settings)
    normal_login.show()
    app.processEvents()
    prompt = [
        item for item in normal_login.findChildren(QLabel, "TerminalPrompt")
        if "AUTHENTICATE" in item.text()
    ]
    assert len(prompt) == 1 and not prompt[0].isVisible()
    assert normal_login.login.isEnabled() and normal_login.submit.isEnabled()
    normal_login.close()

print("CORE TESTS 3.0.26 TERMINAL CODE THEME AND LOGIN OFFSCREEN OK")
