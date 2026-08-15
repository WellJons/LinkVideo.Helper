from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# archive_service only needs QSettings at import time; keep the regression test
# runnable even on a build machine before PySide6 is installed.
qtcore = types.ModuleType("PySide6.QtCore")
class _QSettings:
    def __init__(self, *args, **kwargs): self._data = {}
    def value(self, key, default=None, *args): return self._data.get(key, default)
    def setValue(self, key, value): self._data[key] = value
    def remove(self, key): self._data.pop(key, None)
qtcore.QSettings = _QSettings
pyside = types.ModuleType("PySide6")
pyside.QtCore = qtcore
sys.modules.setdefault("PySide6", pyside)
sys.modules.setdefault("PySide6.QtCore", qtcore)

from linkvideo_vpn_helper.services.archive_service import ArchiveCamera, ArchiveDiscovery, ArchiveService
from linkvideo_vpn_helper.services.vpn_backup_service import VPNBackupService
from linkvideo_vpn_helper.services.vpn_service import (
    ServerAnalysis, SessionCredentials, VPNService, VPN_L2TP_SOFT_LIMIT,
)

service = ArchiveService(_QSettings())
count, duration = service._parse_hls_duration("#EXTM3U\n#EXTINF:2.5,\na.ts\n#EXTINF:3,\nb.ts\n")
assert (count, duration) == (2, 5.5)
assert service._first_nested_playlist("#EXTM3U\nchild/stream.m3u8\n", "https://host/a/master.m3u8") == "https://host/a/child/stream.m3u8"

camera = ArchiveCamera("1", "linkvideo_1", "host", "main/linkvideo_1", "sig", 7, {})
empty = ArchiveDiscovery(camera, 0, 10, [], service._gaps([], 0, 10), [], [])
assert not empty.has_downloadable_archive
assert empty.covered_duration == 0
assert len(empty.gaps) == 1  # UI must not mislabel this as partial archive.

fallback = ArchiveDiscovery(camera, 0, 10, [], [], ["host"], [], [], "https://host/master.m3u8", 6.5, 3, "host", "nested")
assert fallback.has_downloadable_archive
assert fallback.covered_duration == 6.5
assert round(fallback.coverage_percent, 1) == 65.0

backup = VPNBackupService()
folder = Path(tempfile.mkdtemp(prefix="lv_backup_test_"))
payload = {"sections": {"ppp_secrets": [{"name": "u", "password": "p"}], "firewall_nat": [{"dst-port": "10001"}]}}
written = backup.write_server("vpn01.linkvideo.ru", payload, folder, [])
assert written.ok and written.clients_csv and written.clients_csv.exists() and written.nat_csv and written.nat_csv.exists()

vpn = VPNService()
stats = {
    "busy": ServerAnalysis("busy", 1, 100, 50, 50, 100, VPN_L2TP_SOFT_LIMIT, 10, 0),
    "free": ServerAnalysis("free", 20, 100, 50, 50, 100, 200, 10, 0),
    "lessfree": ServerAnalysis("lessfree", 1, 100, 50, 50, 100, 300, 10, 0),
}
vpn.analyze_server_quick = lambda server, creds: stats[server]
assert vpn.pick_best_server_parallel(list(stats), SessionCredentials("u", "p")) == "free"

archive_ui = (ROOT / "linkvideo_vpn_helper" / "ui" / "pages" / "archive_download_page.py").read_text(encoding="utf-8")
assert "if discovery.slices and discovery.gaps:" in archive_ui
assert "self.btn_download.setEnabled(discovery.has_downloadable_archive)" in archive_ui
assert ('MetricCard("Проверено DVR"' in archive_ui or 'MetricCard("Источники"' in archive_ui)

main_ui = (ROOT / "linkvideo_vpn_helper" / "ui" / "main_window.py").read_text(encoding="utf-8")
assert '"vpn_servers"' in main_ui
assert (ROOT / "linkvideo_vpn_helper" / "ui" / "pages" / "vpn_servers_page.py").exists()

print("CORE TESTS 2.1.0 VPN/BACKUP/ARCHIVE OK")
