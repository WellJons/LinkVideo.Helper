from __future__ import annotations

from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]

    service_path = root / "linkvideo_vpn_helper/services/vpn_service.py"
    text = service_path.read_text(encoding="utf-8")
    assert "def _find_client_interface" in text
    assert "def _monitor_interface_traffic" in text
    assert '"interface_name"' in text
    assert "traffic_monitor" in text or "monitor_source" in text

    page = (root / "linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
    assert "Порт не добавлен" in page
    assert "Не удалось" in page
    assert "def _error_text" in page

    main_window = (root / "linkvideo_vpn_helper/ui/main_window.py").read_text(encoding="utf-8")
    assert "SearchManagePage" in main_window

    theme = (root / "linkvideo_vpn_helper/ui/theme.py").read_text(encoding="utf-8")
    assert "QProgressBar" in theme
    assert "QScrollBar" in theme

    status_ui = (root / "linkvideo_vpn_helper/ui/vpn_servers_status_ui.py").read_text(encoding="utf-8")
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
    assert "Google Sheets is disaster-recovery only" in bridge
    assert "notify_mutation" not in bridge
    assert "vpn_final_3_0_13_ux" not in bridge

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
