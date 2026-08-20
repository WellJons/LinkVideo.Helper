from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# LV optional metadata must never break a functionally installed component.
from linkvideo_vpn_helper.services.vpn_automation_resilience import install_vpn_automation_resilience
from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService

install_vpn_automation_resilience()

class FakeAPI:
    def __init__(self):
        self.calls = []

    def set(self, path, rid, params):
        self.calls.append((path, rid, dict(params)))
        raise RuntimeError("failure: item with such name already exists")

fake = FakeAPI()
VPNAutomationService._apply_optional_fields(
    fake,
    "/system/script",
    "*7",
    "LV-AutoRestore",
    [("dont-require-permissions", "no"), ("comment", "LinkVideo.Helper")],
)
assert len(fake.calls) == 2

resilience = (ROOT / "linkvideo_vpn_helper/services/vpn_automation_resilience.py").read_text(encoding="utf-8")
assert 'f"?{field}=": value' in resilience
assert "except Exception:" in resilience

# Manual VPN client scan must yield to the Qt event loop before the real network
# scan starts, so the operator sees feedback immediately.
scan_feedback = (ROOT / "linkvideo_vpn_helper/ui/manual_scan_feedback.py").read_text(encoding="utf-8")
assert 'self.task.busy("Проверяю VPN-серверы"' in scan_feedback
assert "QTimer.singleShot(90, launch)" in scan_feedback
assert "original_scan(self)" in scan_feedback

# Long archive downloads stay inside the page and never claim success for a
# missing/empty MP4. The ambiguous folder-selector label is also gone.
archive_ux = (ROOT / "linkvideo_vpn_helper/ui/archive_download_ux.py").read_text(encoding="utf-8")
components = (ROOT / "linkvideo_vpn_helper/ui/components.py").read_text(encoding="utf-8")
assert 'self.btn_folder.setText("Изменить папку")' in archive_ux
assert "busy_inline" in components
assert "self.task.busy_inline" in archive_ux
assert "path.is_file() and path.stat().st_size > 0" in archive_ux
assert 'path_line = f"Путь: {output}"' in archive_ux
assert "output.parent" in archive_ux

app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
assert "install_vpn_automation_resilience()" in app
assert "install_manual_scan_feedback()" in app
assert "install_archive_download_ux()" in app

print("CORE TESTS 3.0.8 REAL FEEDBACK FIXES OK")
