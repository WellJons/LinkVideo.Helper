from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services import vpn_automation_service as auto_mod
from linkvideo_vpn_helper.services.vpn_automation_service import (
    LV_ACTIVITY_SCHED, LV_AGING_SCHED, LV_RESTORE_SCHED,
    LV_LOG_PREFIX_L2TP, LV_LOG_PREFIX_PPP, VPNAutomationService,
)
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


class FakeAPI:
    stores: dict[str, dict[str, list[dict]]] = {}
    seq = 0

    def __init__(self, host, username, password, port=8728, timeout=5):
        self.host = host
        self.store = self.stores.setdefault(host, {})

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return None
    def _rows(self, path): return self.store.setdefault(path, [])
    def print(self, path, extra_params=None): return [dict(x) for x in self._rows(path)]
    def add(self, path, params):
        type(self).seq += 1
        rid = f"*{type(self).seq}"
        self._rows(path).append({".id": rid, **dict(params)})
        return rid
    def set(self, path, item_id, params):
        for row in self._rows(path):
            if row.get(".id") == item_id:
                row.update(dict(params)); return
        raise AssertionError(f"missing {path} {item_id}")
    def remove(self, path, item_id):
        self.store[path] = [x for x in self._rows(path) if x.get(".id") != item_id]


real_api = auto_mod.RouterOSAPIClient
auto_mod.RouterOSAPIClient = FakeAPI
try:
    svc = VPNAutomationService()
    creds = SessionCredentials("admin", "secret")
    host = "vpn-control.linkvideo.ru"

    # Fresh install: activity+restore ON, quarantine OFF.
    st = svc.install_or_update(host, creds)
    assert st.installed and st.runtime_enabled and not st.aging_enabled and not st.paused
    assert st.state_text == "Работает · карантин выключен"

    # Enable quarantine, stop all automation, then start: previous quarantine state must return.
    st = svc.set_quarantine_enabled(host, creds, True)
    assert st.aging_enabled
    st = svc.set_automation_enabled(host, creds, False)
    assert st.paused and not st.activity_enabled and not st.restore_enabled and not st.aging_enabled
    assert st.state_text == "Остановлено"
    rules = FakeAPI.stores[host]["/system/logging"]
    managed = [r for r in rules if r.get("prefix") in {LV_LOG_PREFIX_PPP, LV_LOG_PREFIX_L2TP}]
    assert managed and all(r.get("disabled") == "yes" for r in managed)

    # Updating while paused must NOT start anything by itself.
    st = svc.install_or_update(host, creds)
    assert st.paused, "update of paused automation must preserve pause"

    st = svc.set_automation_enabled(host, creds, True)
    assert st.runtime_enabled and st.aging_enabled, "resume must restore previous quarantine state"
    managed = [r for r in FakeAPI.stores[host]["/system/logging"] if r.get("prefix") in {LV_LOG_PREFIX_PPP, LV_LOG_PREFIX_L2TP}]
    assert all(r.get("disabled") == "no" for r in managed)

    # Start is idempotent: pressing Start on an already-running server must not
    # silently turn quarantine off.
    st = svc.set_automation_enabled(host, creds, True)
    assert st.runtime_enabled and st.aging_enabled

    # Stop is also idempotent: pressing Stop twice must keep the remembered
    # quarantine state for the next resume.
    svc.set_automation_enabled(host, creds, False)
    svc.set_automation_enabled(host, creds, False)
    st = svc.set_automation_enabled(host, creds, True)
    assert st.runtime_enabled and st.aging_enabled

    # If quarantine was OFF before pause, it stays OFF after resume.
    svc.set_quarantine_enabled(host, creds, False)
    svc.set_automation_enabled(host, creds, False)
    st = svc.set_automation_enabled(host, creds, True)
    assert st.runtime_enabled and not st.aging_enabled

    sched = {r["name"]: r for r in FakeAPI.stores[host]["/system/scheduler"]}
    assert sched[LV_ACTIVITY_SCHED]["disabled"] == "no"
    assert sched[LV_RESTORE_SCHED]["disabled"] == "no"
    assert sched[LV_AGING_SCHED]["disabled"] == "yes"
finally:
    auto_mod.RouterOSAPIClient = real_api

servers_ui = (ROOT / "linkvideo_vpn_helper/ui/pages/vpn_servers_page.py").read_text(encoding="utf-8")
clients_ui = (ROOT / "linkvideo_vpn_helper/ui/pages/inactive_clients_page.py").read_text(encoding="utf-8")
for token in (
    "Памятка состояний VPN-клиентов", "Активная", "Спящая — 30+ дней",
    "Карантин — 90+ дней", "Кандидат в архив — 365+ дней", "Отключена вручную",
    "Активность неизвестна", "Остановить LV", "Запустить LV", "Остановить LV на всех",
    "Запустить LV на всех", "set_automation_enabled",
):
    if token not in servers_ui + clients_ui + (ROOT / "linkvideo_vpn_helper/services/vpn_automation_service.py").read_text(encoding="utf-8"):
        raise AssertionError(f"2.1.2 control/Russian UI contract missing: {token}")

for forbidden in ("Q ON", "Q OFF", "Last Seen:", "Lifecycle"):
    assert forbidden not in servers_ui + clients_ui, f"visible legacy term remains: {forbidden}"

print("CORE TESTS 2.1.2 VPN FULL CONTROL OK")
