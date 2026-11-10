from __future__ import annotations

"""Capture real, offscreen Terminal Code login/workspace for private visual QA.

All credentials are fictional. No backend, RouterOS, or B2O auth is invoked.
"""

import os
import sys
import tempfile
from pathlib import Path

# Invoked as "python scripts/..." by GitHub Actions. Add the repository root
# explicitly, just like the normal release_preflight runner does.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.brand_theme import install_linkvideo_brand_theme
from linkvideo_vpn_helper.ui.visual_density import install_visual_density

app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
install_linkvideo_brand_theme()
install_visual_density()

from linkvideo_vpn_helper.theme import get_theme_style

app.setStyleSheet(get_theme_style("terminal_code"))

out = Path("terminal_preview")
out.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory() as folder:
    settings = QSettings(str(Path(folder) / "preview.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/theme_v2", "terminal_code")
    settings.setValue("remember", False)
    settings.setValue("username", "")
    settings.setValue("password", "")
    settings.setValue("ui/animations_enabled", False)

    from linkvideo_vpn_helper.ui.login_window import LoginWindow

    login = LoginWindow(settings)
    login.resize(1080, 680)
    login.show()
    app.processEvents()
    filename = out / "terminal-code-login.png"
    if not login.grab().save(str(filename)):
        raise RuntimeError(f"Cannot capture {filename}")
    login.close()
    app.processEvents()

    # The normal app shell with the real CreateClientPage widget. Fake local
    # operator identity; no actual API/login/create action is attempted.
    from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService
    from linkvideo_vpn_helper.ui.main_window import MainWindow

    window = MainWindow(VPNService(), SessionCredentials("operator-demo", "not-a-password"), settings)
    window.resize(1380, 900)
    window.show()
    app.processEvents()
    filename = out / "terminal-code-workspace.png"
    if not window.grab().save(str(filename)):
        raise RuntimeError(f"Cannot capture {filename}")
    window.close()

for filename in ("terminal-code-login.png", "terminal-code-workspace.png"):
    image = out / filename
    assert image.is_file() and image.stat().st_size > 10000, image
    print(f"TERMINAL VISUAL QA: {image} ({image.stat().st_size} bytes)")
