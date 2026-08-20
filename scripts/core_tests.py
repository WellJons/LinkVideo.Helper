from __future__ import annotations

import sys
import types
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# archive_service only needs QSettings as a type/storage interface. The CI/build
# has PySide6, but source self-tests should also run in a bare Python checkout.
try:
    import PySide6.QtCore  # noqa: F401
except Exception:
    pyside = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")

    class QSettings:  # minimal test stub
        pass

    qtcore.QSettings = QSettings
    pyside.QtCore = qtcore
    sys.modules.setdefault("PySide6", pyside)
    sys.modules.setdefault("PySide6.QtCore", qtcore)

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient, RouterOSAPIError
from linkvideo_vpn_helper.services.archive_service import ArchiveCamera, ArchiveDiscovery, ArchiveGap, ArchiveService, ArchiveSlice, B2OService, ReserveTransferEvent
from linkvideo_vpn_helper.services.archive_diagnosis_engine import ArchiveDiagnosisEngine, DiagnosisConfidence, DiagnosisSide
from linkvideo_vpn_helper.services.errors import ErrorKind, OperationCancelled, classify_exception
from linkvideo_vpn_helper.services.vpn_service import InactiveClientRecord, VPNService
from linkvideo_vpn_helper.services.search_service import FastSearchService


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# RouterOS protocol length encoder boundaries.
api = RouterOSAPIClient("example", "u", "p")
check(api._encode_length(0x7F) == b"\x7f", "RouterOS 1-byte length")
check(len(api._encode_length(0x80)) == 2, "RouterOS 2-byte length")
check(len(api._encode_length(0x4000)) == 3, "RouterOS 3-byte length")

# RouterOS API standard-port fallback: if plain API/8728 actively refuses,
# Helper must try API-SSL/8729 once and remember it for the current process.
class _FakeSock:
    def close(self):
        pass


fallback_api = RouterOSAPIClient("kz-vpn01.linkvideo.ru", "u", "p", port=8728)
fallback_api._port_cache.clear()
fallback_calls = []

def _fake_open(port):
    fallback_calls.append(port)
    if port == 8728:
        raise ConnectionRefusedError("refused")
    return _FakeSock()

fallback_api._open_socket = _fake_open  # type: ignore[method-assign]
fallback_api.login = lambda: None  # type: ignore[method-assign]
fallback_api.connect()
check(fallback_calls == [8728, 8729], "RouterOS API fallback order")
check(fallback_api.connected_port == 8729, "RouterOS API-SSL fallback")
check(RouterOSAPIClient._port_cache.get("kz-vpn01.linkvideo.ru") == 8729, "RouterOS API port cache")
fallback_api.close()
RouterOSAPIClient._port_cache.clear()

# If both API ports refuse but WinBox itself is reachable, Helper must explain
# that WinBox and RouterOS API are separate services instead of blaming credentials.
closed_api = RouterOSAPIClient("kz-vpn01.linkvideo.ru", "u", "p", port=8728)
closed_api._port_cache.clear()
closed_api._open_socket = lambda port: (_ for _ in ()).throw(ConnectionRefusedError("refused"))  # type: ignore[method-assign]
closed_api._winbox_reachable = lambda timeout=1.2: True  # type: ignore[method-assign]
try:
    closed_api.connect()
    raise AssertionError("expected RouterOSAPIError for closed API ports")
except RouterOSAPIError as exc:
    check("WinBox доступен" in str(exc) and "8728/8729" in str(exc), "WinBox/API diagnostic message")
RouterOSAPIClient._port_cache.clear()

# Error mapping: an auth error must never become an unexplained fatal/API state.
auth = classify_exception(RouterOSAPIError("invalid user name or password"))
check(auth.kind == ErrorKind.AUTH, "RouterOS auth classification")
cancelled = classify_exception(OperationCancelled("stop"))
check(cancelled.kind == ErrorKind.CANCELLED, "operation cancellation classification")

vpn = VPNService()
check(vpn._parse_ports("10001,10003-10005") == [10001, 10003, 10004, 10005], "port parser")
check(vpn._find_free_ports({10001, 10002}, 2) == [10003, 10004], "free port allocation")
check(vpn._next_ip("172.16.1.254") == "172.16.2.1", "remote IP rollover")


# Client snapshot mapping: NAT rule enabled/disabled state is kept, but
# unreliable per-port connection traffic is no longer required by Helper 2.0.
snapshot = {
    "secrets": [{".id": "*1", "name": "8999", "password": "pw", "profile": "8999", "disabled": "no"}],
    "profiles": [{".id": "*2", "name": "8999", "remote-address": "172.16.2.34"}],
    "actives": [{".id": "*3", "name": "8999", "address": "172.16.2.34", "uptime": "1h2m"}],
    "nat_rules": [
        {".id": "*4", "dst-port": "11113", "to-addresses": "172.16.2.34", "comment": "8999", "disabled": "no"},
        {".id": "*5", "dst-port": "11114", "to-addresses": "172.16.2.34", "comment": "8999", "disabled": "yes"},
    ],
    "connections": [],
    "resources": [],
    "interfaces": [{"name": "<l2tp-8999>", "running": "true", "rx-byte": "4096", "tx-byte": "8192"}],
    "traffic_monitors": {},
}
record = vpn._build_client_records("vpn01.linkvideo.ru", snapshot)[0]
check(record.is_online, "client online state")
check(record.ports == [11113, 11114], "client NAT ports")
check(11114 in record.disabled_ports, "disabled port state")
check(record.rx_bytes == 4096 and record.tx_bytes == 8192, "client interface traffic counters")

# Camera ID/operator normalization. Operator IDs are fixed per country.
check(B2OService.normalize_camera_id("linkvideo_207728", 241) == ("207728", "linkvideo_207728"), "RU camera ID normalization")
check(B2OService.normalize_camera_id("268527", 1721) == ("268527", "linkvideokz_268527"), "KZ numeric camera normalization")
check(B2OService.normalize_camera_id("linkvideokz_268527", 241) == ("268527", "linkvideokz_268527"), "KZ prefix overrides selected operator")
check(B2OService.normalize_camera_id("linkvideoby_268552", 241) == ("268552", "linkvideoby_268552"), "BY prefix normalization")
check(B2OService.detect_operator_id("linkvideokz_268527") == 1721, "KZ operator detection")
check(B2OService.detect_operator_id("linkvideoby_268552") == 1741, "BY operator detection")
check(B2OService.operator_country(241) == "Россия" and B2OService.operator_country(1721) == "Казахстан" and B2OService.operator_country(1741) == "Беларусь", "fixed country/operator map")
check(B2OService.operator_cluster(1721) == "linkvideokz" and B2OService.operator_cluster(1741) == "linkvideoby", "regional cluster map")
check("kz-vcore01.video.goodline.info" in B2OService.builtin_archive_servers(1721), "KZ archive server seed")
check("rb-vcore01.video.goodline.info" in B2OService.builtin_archive_servers(1741), "BY archive server seed")
check(B2OService.normalize_vcore_host("b2o-cold-reserve-59") == "b2o-cold-reserve-59.video.goodline.info", "short cold-reserve normalization")


# B2O «Медиа серверы» parser must accept nested/variant JSON layouts and
# normalize short server names without depending on one response schema.
_media_payload = {
    "data": [
        {"server_name": "kz-vcore01.video.goodline.info"},
        {"host": "kz-vcoreA.video.goodline.info"},
        {"name": "b2o-cold-reserve-59"},
        {"title": "not-a-server"},
    ],
    "meta": {"ignored": "value"},
}
_media_hosts = B2OService._extract_dvr_server_hosts(_media_payload)
check("kz-vcore01.video.goodline.info" in _media_hosts, "B2O media server_name parser")
check("kz-vcorea.video.goodline.info" in _media_hosts, "B2O media host parser")
check("b2o-cold-reserve-59.video.goodline.info" in _media_hosts, "B2O short reserve parser")
check("DVR_SERVERS_URL" in B2OService.__dict__, "B2O dvr-servers endpoint contract")

class _Settings:
    def __init__(self):
        self.data = {}
    def value(self, key, default=None, *_args):
        return self.data.get(key, default)
    def setValue(self, key, value):
        self.data[key] = value
    def remove(self, key):
        self.data.pop(key, None)

_b2o = B2OService(_Settings())
_b2o.add_custom_archive_server(1721, "kz-vcore02.video.goodline.info")
check("kz-vcore02.video.goodline.info" in _b2o.archive_servers(1721), "custom KZ archive server")
check("kz-vcore02.video.goodline.info" not in _b2o.archive_servers(1741), "regional archive lists isolated")


_b2o.settings.setValue(
    "archive/dvr_servers_cache/1721",
    '{"timestamp": 9999999999, "items": ["kz-vcore03.video.goodline.info"]}',
)
_b2o._dvr_servers_memory.clear()
check("kz-vcore03.video.goodline.info" in _b2o.archive_servers(1721), "cached B2O media servers merged")

# Six-month client archive relies only on a real RouterOS last-logged-out value.
parsed_last = VPNService.parse_router_datetime("feb/01/2026 12:30:45")
check(parsed_last == datetime(2026, 2, 1, 12, 30, 45), "RouterOS last-logged-out parser")
check(VPNService.parse_router_datetime("never") is None, "unknown inactivity must stay unknown")

# Inactive client archive sorting must stay deterministic for bulk selection UI.
_inactive = [
    InactiveClientRecord("vpn02.linkvideo.ru", "client_b", "", datetime(2026, 1, 10, 10, 0, 0), True, ""),
    InactiveClientRecord("vpn01.linkvideo.ru", "client_c", "", datetime(2025, 12, 1, 10, 0, 0), True, ""),
    InactiveClientRecord("vpn01.linkvideo.ru", "client_a", "", datetime(2026, 2, 1, 10, 0, 0), False, ""),
]
check([x.login for x in VPNService.sort_inactive_records(_inactive, "old")] == ["client_c", "client_b", "client_a"], "inactive sort old first")
check([x.login for x in VPNService.sort_inactive_records(_inactive, "new")] == ["client_a", "client_b", "client_c"], "inactive sort new first")
check([x.login for x in VPNService.sort_inactive_records(_inactive, "server")] == ["client_c", "client_a", "client_b"], "inactive sort by server")
check([x.login for x in VPNService.sort_inactive_records(_inactive, "login")] == ["client_a", "client_b", "client_c"], "inactive sort by login")

# Login search should find the exact login and its per-server generated suffixes.
check(FastSearchService._login_matches_query("89950639067", "89950639067"), "exact login search")
check(FastSearchService._login_matches_query("89950639067_3", "89950639067"), "suffix login search")
check(not FastSearchService._login_matches_query("189950639067", "89950639067"), "unrelated login must not match")

# reserve-transfers uses UTC+7 independently from the camera timezone.
# Camera UTC+3 05:00-05:05 == reserve log UTC+7 09:00-09:05.
cam_start = datetime(2026, 8, 9, 5, 0, 0, tzinfo=timezone(timedelta(hours=3))).timestamp()
cam_end = datetime(2026, 8, 9, 5, 5, 0, tzinfo=timezone(timedelta(hours=3))).timestamp()
reserve_move = B2OService._parse_reserve_dt("09.08.2026/09:03:00")
expected_move = datetime(2026, 8, 9, 5, 3, 0, tzinfo=timezone(timedelta(hours=3))).timestamp()
check(ArchiveService.reserve_log_period(cam_start, cam_end) == ("09.08.2026 09:00:00", "09.08.2026 09:05:00"), "reserve UTC+7 window")
check(reserve_move == expected_move, "reserve UTC+7 move equals camera UTC+3 instant")

# Archive plan must preserve gaps and prefer reserve when overlap starts equally.
main = ArchiveSlice("main.video.goodline.info", "vcrf", "cam_main", 100, 180, "sig", "main")
reserve = ArchiveSlice("reserve.video.goodline.info", "vcrf", "cam_main", 100, 150, "sig", "reserve")
late = ArchiveSlice("main.video.goodline.info", "vcrf", "cam_main", 200, 240, "sig", "main")
plan = ArchiveService._build_plan([main, reserve, late], 100, 240)
check(plan[0].start == 100 and plan[0].end == 180, "archive plan longest coverage")
gaps = ArchiveService._gaps(plan, 100, 240)
check(len(gaps) == 1 and gaps[0].start == 180 and gaps[0].end == 200, "archive gap detection")

url = ArchiveService.playlist_url(ArchiveSlice("v.example", "vcrf", "cam", 100, 160, "abc", "main"))
check("playlist_dvr_range-100-60.m3u8?wmsAuthSign=abc" in url, "signed playlist URL")


# Archive diagnosis engine must infer a side only from comparative evidence.
def _disc(label: str, gaps: list[tuple[float, float]], server: str = "b2o-vcore10.video.goodline.info"):
    cam = ArchiveCamera(label.replace("linkvideo_", ""), label, server, label, "sig", 3)
    requested_start, requested_end = 1000.0, 1300.0
    gap_objs = [ArchiveGap(a, b) for a, b in gaps]
    # Build covered slices around gaps so coverage is internally consistent.
    points = [(requested_start, requested_end)]
    slices = []
    cursor = requested_start
    for gap in sorted(gap_objs, key=lambda g: g.start):
        if gap.start > cursor:
            slices.append(ArchiveSlice(server, "vcrf", label, cursor, gap.start, "sig", "compare"))
        cursor = max(cursor, gap.end)
    if cursor < requested_end:
        slices.append(ArchiveSlice(server, "vcrf", label, cursor, requested_end, "sig", "compare"))
    return ArchiveDiscovery(cam, requested_start, requested_end, slices, gap_objs, [server], [])

engine = ArchiveDiagnosisEngine()
main_d = _disc("linkvideo_1", [(1100, 1160)])
address_a = _disc("linkvideo_2", [(1102, 1158)])
address_b = _disc("linkvideo_3", [(1099, 1161)])
server_ok1 = _disc("linkvideo_10", [])
server_ok2 = _disc("linkvideo_11", [])
server_ok3 = _disc("linkvideo_12", [])
res = engine.analyze(main_d, [("2", address_a, None), ("3", address_b, None)], [("10", server_ok1, None), ("11", server_ok2, None), ("12", server_ok3, None)])
check(res.side == DiagnosisSide.CLIENT_SITE and res.confidence in (DiagnosisConfidence.HIGH, DiagnosisConfidence.MEDIUM), "diagnosis client-site correlation")

server_bad1 = _disc("linkvideo_20", [(1101, 1159)])
server_bad2 = _disc("linkvideo_21", [(1104, 1157)])
server_bad3 = _disc("linkvideo_22", [(1098, 1162)])
res = engine.analyze(main_d, [("2", server_ok1, None), ("3", server_ok2, None)], [("20", server_bad1, None), ("21", server_bad2, None), ("22", server_bad3, None), ("23", server_ok3, None)])
check(res.side == DiagnosisSide.SERVER, "diagnosis server mass correlation")

res = engine.analyze(main_d, [("2", server_ok1, None), ("3", server_ok2, None)], [("10", server_ok1, None), ("11", server_ok2, None), ("12", server_ok3, None)])
check(res.side == DiagnosisSide.CAMERA, "diagnosis isolated camera correlation")

# A tiny accidental overlap must not be treated as synchronized server outage.
server_tiny1 = _disc("linkvideo_40", [(1158, 1162)])
server_tiny2 = _disc("linkvideo_41", [(1159, 1163)])
server_tiny3 = _disc("linkvideo_42", [(1157, 1161)])
res = engine.analyze(main_d, [("2", server_ok1, None), ("3", server_ok2, None)], [("40", server_tiny1, None), ("41", server_tiny2, None), ("42", server_tiny3, None)])
check(res.side != DiagnosisSide.SERVER, "diagnosis ignores tiny accidental overlap")

main_move = _disc("linkvideo_30", [(1100, 1160)])
main_move.reserve_events = [ReserveTransferEvent("old", "new", 1110, 1120)]
res = engine.analyze(main_move, [], [])
check(res.side == DiagnosisSide.MOVE and res.confidence == DiagnosisConfidence.HIGH, "diagnosis reserve move correlation")


print("CORE TESTS OK")

# ---------------------------------------------------------------------------
# 2.0.2 regression tests: updater/version normalization and RouterOS rollback.
# ---------------------------------------------------------------------------
from linkvideo_vpn_helper.services.update_service import _version_tuple
import linkvideo_vpn_helper.services.vpn_service as vpn_module
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials

check(_version_tuple("2.0.2") == _version_tuple("2.0.2.0"), "update version trailing zero normalization")
check(_version_tuple("2.0.2\x00") == _version_tuple("2.0.2"), "Windows ProductVersion embedded NUL normalization")
check(_version_tuple("2.0.3") > _version_tuple("2.0.2.99"), "update version ordering")

# Updater must reject wrong hashes and accept the exact release bytes.
import hashlib
import tempfile
from linkvideo_vpn_helper.services.update_service import UpdateService

with tempfile.TemporaryDirectory(prefix="lv_update_test_") as _td:
    _src = Path(_td) / "fake_setup.exe"
    _src.write_bytes(b"MZ" + b"X" * (70 * 1024))
    _hash = hashlib.sha256(_src.read_bytes()).hexdigest()
    _svc = UpdateService("file:///unused")
    _downloaded = _svc.download_setup(_src.as_uri(), expected_sha256=_hash)
    check(_downloaded.exists() and _downloaded.read_bytes() == _src.read_bytes(), "updater accepts exact SHA256")
    try:
        _svc.download_setup(_src.as_uri(), expected_sha256="0" * 64)
        raise AssertionError("expected SHA256 mismatch")
    except RuntimeError as exc:
        check("SHA-256" in str(exc), "updater rejects wrong SHA256")


_original_router_client = vpn_module.RouterOSAPIClient
try:
    class _RollbackRouter:
        removed = []
        added = []
        mode = "secret_fail"
        nat_add_count = 0
        recreate_add_count = 0

        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def print(self, path, params=None):
            return []
        def add(self, path, data):
            type(self).added.append((path, dict(data)))
            if type(self).mode == "secret_fail":
                if path == "/ppp/profile":
                    return "profile-created"
                if path == "/ppp/secret":
                    raise RuntimeError("simulated secret failure")
            if type(self).mode == "ports_fail":
                if path == "/ip/firewall/nat":
                    type(self).nat_add_count += 1
                    if type(self).nat_add_count == 1:
                        return "nat-created-1"
                    raise RuntimeError("simulated second NAT failure")
            if type(self).mode == "recreate_fail":
                if path == "/ip/firewall/nat":
                    type(self).recreate_add_count += 1
                    if type(self).recreate_add_count == 1:
                        raise RuntimeError("simulated recreate failure")
                    return "nat-restored"
            return "id-created"
        def remove(self, path, item_id):
            type(self).removed.append((path, item_id))
            if type(self).mode == "rollback_fail" and item_id == "nat-fail":
                raise RuntimeError("simulated rollback failure")
        def set(self, path, item_id, data):
            pass

    vpn_module.RouterOSAPIClient = _RollbackRouter
    creds = SessionCredentials("u", "p")

    # Profile must be rolled back even when Secret creation fails immediately after it.
    _RollbackRouter.mode = "secret_fail"
    _RollbackRouter.removed = []
    _RollbackRouter.added = []
    v = VPNService()
    v.fetch_config_snapshot = lambda *_args, **_kwargs: {
        "secrets": [], "actives": [], "profiles": [], "nat_rules": [],
        "connections": [], "resources": [], "interfaces": [], "traffic_monitors": {},
    }
    try:
        v.create_clients_batch("vpn-test", creds, "rollback_user", 1, 1)
        raise AssertionError("expected simulated secret failure")
    except RuntimeError as exc:
        check("simulated secret failure" in str(exc), "create rollback test trigger")
    check(("/ppp/profile", "profile-created") in _RollbackRouter.removed, "PPP profile rollback after Secret failure")

    # Adding multiple NAT ports must be atomic from Helper's point of view.
    _RollbackRouter.mode = "ports_fail"
    _RollbackRouter.removed = []
    _RollbackRouter.added = []
    _RollbackRouter.nat_add_count = 0
    v = VPNService()
    client = ClientRecord("vpn-test", "rollback_user", "pw", "172.16.1.176", [])
    v.get_client = lambda *_args, **_kwargs: client
    try:
        v.add_ports("vpn-test", creds, "rollback_user", 2)
        raise AssertionError("expected simulated second NAT failure")
    except RuntimeError as exc:
        check("second NAT failure" in str(exc), "NAT rollback test trigger")
    check(("/ip/firewall/nat", "nat-created-1") in _RollbackRouter.removed, "partial NAT add rollback")

    # Recreate must restore the old rule if creating the replacement fails.
    _RollbackRouter.mode = "recreate_fail"
    _RollbackRouter.removed = []
    _RollbackRouter.added = []
    _RollbackRouter.recreate_add_count = 0
    v = VPNService()
    v.fetch_config_snapshot = lambda *_args, **_kwargs: {
        "secrets": [], "actives": [], "profiles": [],
        "nat_rules": [{
            ".id": "old-nat", "chain": "dstnat", "protocol": "tcp",
            "dst-port": "10001", "action": "dst-nat", "to-addresses": "172.16.1.176",
            "to-ports": "10001", "comment": "rollback_user", "disabled": "no",
        }],
        "connections": [], "resources": [], "interfaces": [], "traffic_monitors": {},
    }
    v._build_client_records = lambda *_args, **_kwargs: [client]
    try:
        v.recreate_port("vpn-test", creds, "rollback_user", 10001)
        raise AssertionError("expected simulated recreate failure")
    except RuntimeError as exc:
        check("simulated recreate failure" in str(exc), "recreate rollback test trigger")
    check(("/ip/firewall/nat", "old-nat") in _RollbackRouter.removed, "old NAT removed before recreate")
    check(_RollbackRouter.recreate_add_count == 2, "old NAT restored after recreate failure")

    # Rollback must continue after one failed deletion and must report the
    # incomplete cleanup instead of silently claiming the original operation
    # was atomic.
    _RollbackRouter.mode = "rollback_fail"
    _RollbackRouter.removed = []
    v = VPNService()
    try:
        v._rollback_create("vpn-test", creds, "profile-created", "secret-created", ["nat-fail", "nat-ok"])
        raise AssertionError("expected simulated rollback failure")
    except RuntimeError as exc:
        check("simulated rollback failure" in str(exc), "rollback failure is surfaced")
    check(("/ip/firewall/nat", "nat-ok") in _RollbackRouter.removed, "rollback continues after NAT removal failure")
    check(("/ppp/secret", "secret-created") in _RollbackRouter.removed, "rollback still removes Secret")
    check(("/ppp/profile", "profile-created") in _RollbackRouter.removed, "rollback still removes Profile")
finally:
    vpn_module.RouterOSAPIClient = _original_router_client

print("CORE TESTS 2.0.2 REGRESSIONS OK")
