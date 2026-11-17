"""LinkVideo.Helper 4 Native Preview, based on the released PySide6 app."""
from __future__ import annotations
import os
import sys
os.environ["LV4_NATIVE"]="1"
from linkvideo_vpn_helper import version
version.APP_NAME="LinkVideo.Helper 4 Native Preview"
version.APP_VERSION="4.0.0-alpha.14"

def smoke()->int:
    os.environ["QT_QPA_PLATFORM"]="offscreen"
    from PySide6.QtWidgets import QApplication,QMainWindow
    from PySide6.QtCore import QSettings
    from linkvideo_vpn_helper.services.vpn_service import VPNService,SessionCredentials
    from linkvideo_vpn_helper.ui.main_window import MainWindow
    app=QApplication.instance() or QApplication(["native4","-platform","offscreen"])
    app.setApplicationName(version.APP_NAME)
    settings=QSettings("LinkVideo","LinkVideo.Helper.Native4.Smoke")
    window=MainWindow(VPNService(),SessionCredentials("fixture","fixture"),settings)
    assert isinstance(window,QMainWindow) and window.minimumWidth()>=900
    assert len(window.NAV_ITEMS)>=9
    assert window._current_key=="create"
    window.close()
    return 0

if __name__=="__main__":
    if "--lv4-native-smoke" in sys.argv:
        raise SystemExit(smoke())
    if any(arg.startswith("--lv4-background-") for arg in sys.argv[1:]):
        raise SystemExit("Native4 background task disabled in preview.")
    from linkvideo_vpn_helper.app import main
    raise SystemExit(main())
