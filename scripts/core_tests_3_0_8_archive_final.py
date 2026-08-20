from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.archive_service import ArchiveCamera, ArchiveService, ArchiveSlice


class Settings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None, *args):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)


service = ArchiveService(Settings())
service._history_hosts = lambda camera_id: ["camera-history.video.goodline.info"]
service._global_history_hosts = lambda operator_id: ["stale-global.video.goodline.info"]
service.b2o.archive_servers = lambda operator_id, online=True: ["live-b2o.video.goodline.info"]

hosts = service._deep_candidate_hosts("primary.video.goodline.info", 241, "123")
assert hosts[:3] == [
    "camera-history.video.goodline.info",
    "live-b2o.video.goodline.info",
    "stale-global.video.goodline.info",
], hosts

service.DEEP_FALLBACK_DEADLINE_SECONDS = 0.15
service.DEEP_FALLBACK_MAX_WORKERS = 2
service._deep_candidate_hosts = lambda *args, **kwargs: [
    "good.video.goodline.info",
    "slow1.video.goodline.info",
    "slow2.video.goodline.info",
]
service.b2o.resolve_operator_id = lambda *args, **kwargs: 241

camera = ArchiveCamera(
    camera_id="123",
    label="linkvideo_123",
    server="primary.video.goodline.info",
    stream_name="123",
    signature="",
    timezone_offset=7,
    raw={},
    candidate_hosts=[],
)


def probe(host, camera, start_ts, end_ts, timeout):
    if host.startswith("good"):
        time.sleep(0.02)
        slices = [ArchiveSlice(host, "main", "123", start_ts, end_ts, "", "deep")]
        return {"slices": slices, "hosts": [], "host": host}, []
    time.sleep(0.45)
    return None, ["slow"]


service._probe_hls_host = probe
start = time.monotonic()
found, checked, count = service._deep_search_missing(camera, 1_000.0, 1_010.0, [], set())
elapsed = time.monotonic() - start
assert elapsed < 0.25, elapsed
assert found and found[0].host == "good.video.goodline.info"
assert count >= 1
assert "good.video.goodline.info" in checked

source = (ROOT / "linkvideo_vpn_helper/services/archive_service.py").read_text(encoding="utf-8")
assert "ThreadPoolExecutor(" not in source
assert "from concurrent.futures" not in source
assert "queue.Queue" in source
assert "threading.Semaphore" in source
assert "daemon=True" in source
assert "stop_when=deep_covers_interval" in source
assert "stop_when=reserve_covers_interval" in source
# Preserve the real ArchiveCamera API while overriding discover().
assert "camera.timezone_offset" in source
assert "camera.utc_offset_hours" not in source

print("CORE TESTS 3.0.8 ARCHIVE BOUNDED FALLBACK OK")
