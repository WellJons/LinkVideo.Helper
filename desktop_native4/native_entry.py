"""LinkVideo.Helper 4 Native Preview, based on the released PySide6 app."""
from __future__ import annotations
import os
import sys
from pathlib import Path
# Running from source must see the repository modules, not only this subdir.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ["LV4_NATIVE"]="1"
from linkvideo_vpn_helper import version
version.APP_NAME="LinkVideo.Helper 4 Native Preview"
version.APP_VERSION="4.0.0-alpha.14"

def smoke()->int:
    os.environ["QT_QPA_PLATFORM"]="offscreen"
    from PySide6.QtWidgets import QApplication,QMainWindow
    from PySide6.QtCore import QSettings
    import tempfile
    from pathlib import Path
    from linkvideo_vpn_helper.ui.login_window import LoginWindow
    from linkvideo_vpn_helper.services.vpn_service import VPNService,SessionCredentials
    from linkvideo_vpn_helper.ui.main_window import MainWindow
    app=QApplication.instance() or QApplication(["native4","-platform","offscreen"])
    app.setApplicationName(version.APP_NAME)
    temp=tempfile.TemporaryDirectory()
    settings=QSettings(str(Path(temp.name)/"native4-smoke.ini"),QSettings.Format.IniFormat)
    login=LoginWindow(settings)
    assert login.minimumWidth()>=730
    window=MainWindow(VPNService(),SessionCredentials("fixture","fixture"),settings)
    assert isinstance(window,QMainWindow) and window.minimumWidth()>=900
    assert len(window.NAV_ITEMS)>=9
    assert window._current_key=="create"
    shot=os.environ.get("LV4_NATIVE_SCREENSHOT")
    if shot:
        directory=Path(shot);directory.mkdir(parents=True,exist_ok=True)
        login.show();app.processEvents();assert login.grab().save(str(directory/"login.png"))
        login.close();window.show();app.processEvents()
        assert window.grab().save(str(directory/"workspace.png"))
    window.close();temp.cleanup()
    return 0

if __name__=="__main__":
    if "--lv4-native-smoke" in sys.argv:
        raise SystemExit(smoke())
    if any(arg.startswith("--lv4-background-") for arg in sys.argv[1:]):
        raise SystemExit("Native4 background task disabled in preview.")
    from linkvideo_vpn_helper.app import main
    raise SystemExit(main())
