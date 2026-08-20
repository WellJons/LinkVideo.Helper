from __future__ import annotations

from pathlib import Path
import re

from linkvideo_vpn_helper.services import vpn_automation_service as automation_module
from linkvideo_vpn_helper.services import vpn_automation_service_core as automation_core


class AutomationAPI:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {
            "/system/script": [],
            "/system/scheduler": [],
            "/system/logging/action": [],
            "/system/logging": [],
        }
        self.added: list[tuple[str, dict]] = []
        self.sets: list[tuple[str, str, dict]] = []
        self.seq = 0

    def talk(self, command, params=None, raise_on_trap=True):
        if command.endswith("/find"):
            raise RuntimeError("no such command")
        raise AssertionError((command, params))

    def print(self, path, params=None):
        return [dict(row) for row in self.rows.setdefault(path, [])]

    def add(self, path, params):
        params = dict(params)
        self.seq += 1
        row = dict(params)
        if path == "/system/logging/action":
            name = str(params.get("name", ""))
            assert re.fullmatch(r"[A-Za-z0-9]+", name), name
            assert set(params) <= {"name", "target"}, params
        if path == "/system/logging":
            assert params.get("action") == automation_module.LV_LOG_ACTION, params
            assert params.get("topics") in {"ppp", "l2tp"}, params
            assert "prefix" not in params, params
            assert "regex" not in params, params
            self.rows[path].append(row)
            self.added.append((path, params))
            return ""

        row[".id"] = f"*{self.seq:X}"
        self.rows[path].append(row)
        self.added.append((path, params))
        return ""

    def set(self, path, rid, params):
        params = dict(params)
        self.sets.append((path, rid, params))
        optional_unsupported = {
            ("/system/script", "dont-require-permissions"),
            ("/system/logging/action", "memory-lines"),
            ("/system/logging/action", "memory-stop-on-full"),
            ("/system/logging", "regex"),
            ("/system/scheduler", "start-time"),
        }
        if len(params) == 1 and (path, next(iter(params))) in optional_unsupported:
            raise RuntimeError("unknown parameter")
        for row in self.rows.setdefault(path, []):
            if row.get(".id") == rid:
                row.update(params)
                return

    def enable(self, path, rid):
        self.set(path, rid, {"disabled": "no"})

    def disable(self, path, rid):
        self.set(path, rid, {"disabled": "yes"})


def main() -> None:
    assert automation_module.LV_LOG_ACTION == "LVAuth"
    assert re.fullmatch(r"[A-Za-z0-9]+", automation_module.LV_LOG_ACTION)
    assert automation_core.LV_LOG_ACTION == automation_module.LV_LOG_ACTION
    assert f'buffer="{automation_module.LV_LOG_ACTION}"' in automation_core.restore_script_source()

    automation_api = AutomationAPI()
    automation_module.VPNAutomationService()._ensure_components(automation_api, preserve_pause=False)
    assert len([1 for path, _params in automation_api.added if path == "/system/script"]) == 3
    assert len([1 for path, _params in automation_api.added if path == "/system/scheduler"]) == 3
    logging = [params for path, params in automation_api.added if path == "/system/logging"]
    assert len(logging) == 2, logging
    assert {row["topics"] for row in logging} == {"ppp", "l2tp"}
    assert all(row["action"] == "LVAuth" for row in logging)
    assert all("prefix" not in row for row in logging)
    assert any(path == "/system/logging/action" for path, _params in automation_api.added)

    root = Path(__file__).resolve().parents[1]
    visual = (root / "linkvideo_vpn_helper/ui/search_visual_fixes.py").read_text(encoding="utf-8")
    assert "Сервер: {client.server}" in visual
    assert "Страна: {country}" in visual
    assert "Портов: {len(client.ports)}" in visual
    assert "max(106, hinted.height() + 6)" in visual
    assert "font-weight: 600" in visual

    density = (root / "linkvideo_vpn_helper/ui/visual_density.py").read_text(encoding="utf-8")
    assert "geometry intentionally unchanged" in density
    assert "QFrame#SubtleCard" in density
    assert "QTableWidget" in density
    assert 'QLabel[pill="success"]' in density
    assert "QListWidget#PortList::item:selected" in density

    app = (root / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
    for marker in (
        "install_visual_density()",
        "install_vpn_servers_status_ui()",
        "install_vpn_servers_manual_refresh()",
        "install_search_escape_compat()",
        "install_nested_scroll_guard()",
        "install_nat_counter_ui()",
        "install_uptime_ru()",
        "install_retention_policy()",
        "install_vpn_sheets_retention_compat()",
        "install_vpn_sheets_resilience()",
        "install_vpn_sheets_coordinator_resilience()",
        "install_vpn_automation_sheets_bridge()",
    ):
        assert marker in app, marker
    assert "install_quarantine_runtime_fix()" not in app
    assert "install_inline_port_traffic" not in app
    assert "port_traffic_service" not in app

    status_ui = (root / "linkvideo_vpn_helper/ui/vpn_servers_status_ui.py").read_text(encoding="utf-8")
    assert "status_item.setText(auto.state_text)" in status_ui
    assert "365+" in status_ui and "удал" in status_ui

    manual = (root / "linkvideo_vpn_helper/ui/vpn_servers_manual_refresh.py").read_text(encoding="utf-8")
    assert "timer.stop()" in manual
    assert "Нажмите «Обновить данные»" in manual
    assert "self.refresh()" not in manual

    esc = (root / "linkvideo_vpn_helper/ui/search_escape_compat.py").read_text(encoding="utf-8")
    assert "self._close_client_view()" in esc
    assert "self.query.setFocus()" in esc

    scroll = (root / "linkvideo_vpn_helper/ui/nested_scroll_guard.py").read_text(encoding="utf-8")
    assert "QAbstractScrollArea" in scroll
    assert "event.accept()" in scroll

    uptime = (root / "linkvideo_vpn_helper/ui/uptime_ru_compat.py").read_text(encoding="utf-8")
    assert "Время подключения" in uptime
    assert "мин" in uptime

    bridge = (root / "linkvideo_vpn_helper/ui/vpn_automation_sheets_bridge.py").read_text(encoding="utf-8")
    assert "notify_mutation" in bridge
    assert "включение автокарантина" in bridge

    sheets_resilience = (root / "linkvideo_vpn_helper/services/vpn_sheets_resilience.py").read_text(encoding="utf-8")
    assert "_MAX_ATTEMPTS = 3" in sheets_resilience
    assert "_lv_read_timeout" in sheets_resilience
    assert "prepare_sync" in sheets_resilience
    assert "GoogleSheetsUncertainWriteError" in sheets_resilience

    coordinator = (root / "linkvideo_vpn_helper/ui/vpn_sheets_coordinator_resilience.py").read_text(encoding="utf-8")
    assert "worker_count = min(2" in coordinator
    assert "prepare_sync" in coordinator
    assert "time.monotonic() + 75.0" not in coordinator
    assert "friendly_google_error" in coordinator

    print("CORE TESTS FIELD FEEDBACK 3.0.10 OK")


if __name__ == "__main__":
    main()
