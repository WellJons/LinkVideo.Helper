from __future__ import annotations

import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials
from linkvideo_vpn_helper.services import vpn_automation_service as vas

# --- Search regression: one dead VPN must not pin the progress overlay forever. ---
class _SearchVPN:
    def get_client(self, server, creds, login):
        return ClientRecord(server=server, login=login, password="", remote_address="", ports=[])

search = FastSearchService(_SearchVPN(), max_workers=2)

def fake_match(server, creds, query):
    if server == "dead.example":
        time.sleep(0.35)
        return []
    return [query] if server == "good.example" else []

search._server_matching_logins = fake_match  # type: ignore[attr-defined]
start = time.monotonic()
report = search.search_login_all(
    ["good.example", "dead.example"],
    SessionCredentials("u", "p", timeout=0.1),
    "89000000000",
    deadline_seconds=0.07,
)
elapsed = time.monotonic() - start
assert elapsed < 0.25, elapsed
assert any(x.server == "dead.example" for x in report.errors)
assert any(x.server == "good.example" and x.login == "89000000000" for x in report.matches)
assert report.checked == report.total == 2
print("CORE TESTS 3.0.7 SEARCH DEADLINE OK")

# --- VPN Automation regression: a legacy partial install (scripts only) is repairable by Start. ---
class _Store:
    menus = {
        "/system/script": [],
        "/system/scheduler": [],
        "/system/logging/action": [],
        "/system/logging": [],
        "/ppp/secret": [],
        "/system/device-mode": [{".id": "*dm", "mode": "advanced", "scheduler": "yes", "flagged": "no"}],
    }
    seq = 1


def _new_id():
    value = f"*{_Store.seq}"
    _Store.seq += 1
    return value

for name in (vas.LV_ACTIVITY_SCRIPT, vas.LV_AGING_SCRIPT, vas.LV_RESTORE_SCRIPT):
    _Store.menus["/system/script"].append({
        ".id": _new_id(),
        "name": name,
        "source": "old",
        "comment": f"LinkVideo.Helper LV Automation {vas.LV_AUTOMATION_VERSION}",
        "disabled": "no",
    })

class FakeAPI:
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def print(self, path, extra_params=None):
        return [dict(x) for x in _Store.menus.setdefault(path, [])]
    def add(self, path, params):
        row = {".id": _new_id(), **{k: str(v) for k, v in params.items()}}
        _Store.menus.setdefault(path, []).append(row)
        return row[".id"]
    def set(self, path, item_id, params):
        for row in _Store.menus.setdefault(path, []):
            if row.get(".id") == item_id:
                row.update({k: str(v) for k, v in params.items()})
                return
        raise RuntimeError(f"missing {path} {item_id}")
    def enable(self, path, item_id): self.set(path, item_id, {"disabled": "no"})
    def disable(self, path, item_id): self.set(path, item_id, {"disabled": "yes"})

orig_api = vas.RouterOSAPIClient
vas.RouterOSAPIClient = FakeAPI
try:
    auto = vas.VPNAutomationService()
    before = auto.get_status("vpn01", SessionCredentials("u", "p"))
    assert before.scripts_ready and not before.installed
    status = auto.set_automation_enabled("vpn01", SessionCredentials("u", "p"), True)
    assert status.installed, status.installation_detail
    assert status.runtime_enabled
    assert status.activity_enabled and status.restore_enabled
    assert not status.aging_enabled  # quarantine remains opt-in
finally:
    vas.RouterOSAPIClient = orig_api
print("CORE TESTS 3.0.7 VPN AUTOMATION REPAIR OK")

# Static UI contract: background search exceptions must still emit a completion report,
# and 100% is reserved for the real completion signal.
ui = (ROOT / "linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
assert "Never leave the modal busy overlay alive" in ui
assert "min(95" in ui
vpn_ui = (ROOT / "linkvideo_vpn_helper/ui/pages/vpn_servers_page.py").read_text(encoding="utf-8")
assert "auto.installed or auto.scripts_ready" in vpn_ui
print("CORE TESTS 3.0.7 UI COMPLETION CONTRACT OK")
