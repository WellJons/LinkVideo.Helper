from __future__ import annotations

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import PySide6.QtCore  # noqa
except Exception:
    pyside = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")
    class QSettings:
        pass
    qtcore.QSettings = QSettings
    pyside.QtCore = qtcore
    sys.modules.setdefault("PySide6", pyside)
    sys.modules.setdefault("PySide6.QtCore", qtcore)

from linkvideo_vpn_helper.services.archive_service import ArchiveService


def main():
    source = (ROOT / "linkvideo_vpn_helper/services/archive_service.py").read_text(encoding="utf-8")
    settings_source = (ROOT / "linkvideo_vpn_helper/ui/pages/settings_page.py").read_text(encoding="utf-8")
    assert 'range(1, 501)' not in source
    assert 'Ищу архив на прошлых vcore' not in source
    assert 'Архивные серверы' not in settings_source

    playlist = """#EXTM3U
#EXT-X-PROGRAM-DATE-TIME:2026-08-15T07:00:00Z
#EXTINF:10.0,
https://b2o-vcore116.video.goodline.info/dvr_v_p1/a.ts
#EXTINF:10.0,
https://b2o-vcore116.video.goodline.info/dvr_v_p1/b.ts
#EXT-X-PROGRAM-DATE-TIME:2026-08-15T07:00:20Z
#EXTINF:10.0,
https://mass-vcore28.video.goodline.info/dvr_v_p2/c.ts
"""
    seg = ArchiveService._parse_hls_segments(
        playlist,
        "https://b2o-vcore999.video.goodline.info/main/cam/chunks_dvr_range-x.m3u8",
        0,
    )
    assert [x["host"] for x in seg] == [
        "b2o-vcore116.video.goodline.info",
        "b2o-vcore116.video.goodline.info",
        "mass-vcore28.video.goodline.info",
    ]
    slices = ArchiveService._slices_from_hls_segments(seg, "main", "cam", "sig")
    assert len(slices) == 2, slices
    assert slices[0].host == "b2o-vcore116.video.goodline.info"
    assert slices[1].host == "mass-vcore28.video.goodline.info"
    assert round(sum(x.duration for x in slices)) == 30
    print("CORE TESTS 3.0.3 ARCHIVE FAST ROUTE OK")


if __name__ == "__main__":
    main()
