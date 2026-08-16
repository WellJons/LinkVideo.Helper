from __future__ import annotations

"""Small UI corrections for the VPN-server automation status table."""


_INSTALLED = False


def _refresh_automation_cells(page) -> None:
    table = getattr(page, "table", None)
    statuses = getattr(page, "_automation_by_host", {}) or {}
    if table is None:
        return

    for row in range(table.rowCount()):
        host_item = table.item(row, 0)
        status_item = table.item(row, 7)
        if host_item is None or status_item is None:
            continue
        auto = statuses.get(host_item.text())
        if auto is None:
            continue

        # AutomationStatus.state_text already contains the quarantine state.
        # vpn_servers_page historically appended it once more, producing
        # "карантин включён · карантин включён" in the table.
        status_item.setText(auto.state_text)
        if auto.installed and not auto.paused:
            status_item.setToolTip(
                "LV-Aging включён и запускается раз в сутки в 03:20. "
                "Счётчики карантина/архива меняются после запуска LV-Aging."
                if auto.aging_enabled
                else
                "LV-Aging выключен. Новые учётки автоматически в карантин не переводятся."
            )
        else:
            status_item.setToolTip("")


def install_vpn_servers_status_ui() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    original_on_stats = VPNServersPage._on_stats

    def patched_on_stats(self, rows):
        original_on_stats(self, rows)
        _refresh_automation_cells(self)

    VPNServersPage._on_stats = patched_on_stats
    _INSTALLED = True
