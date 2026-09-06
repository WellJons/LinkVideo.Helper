from __future__ import annotations

"""Schedule a Google Sheets reconciliation after LV automation mutations.

Operator actions are mirrored to the temporary Sheets backend within seconds.
The final 3.0.13 layer installed from this module replaces the old recurring
five-minute full scan with RouterOS change events plus a manual safety-net sync.
"""


_INSTALLED = False


def install_vpn_automation_sheets_bridge() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    original = VPNServersPage._on_action

    def patched(self, name: str, payload, error):
        original(self, name, payload, error)
        if error:
            return
        try:
            from linkvideo_vpn_helper.ui import vpn_sheets_sync_integration as integration
            coordinator = getattr(integration, "_COORDINATOR", None)
            if coordinator is None or not coordinator.is_configured():
                return

            reasons = {
                "install": "обновление LV-автоматики",
                "seed": "инициализация lifecycle",
                "quarantine_on": "включение автокарантина",
                "quarantine_off": "выключение автокарантина",
                "automation_on": "запуск LV-автоматики",
                "automation_off": "остановка LV-автоматики",
            }

            if isinstance(payload, dict) and payload.get("host"):
                host = str(payload.get("host") or "").strip()
                if host and name in reasons:
                    coordinator.notify_mutation(host, reasons[name])
                return

            if name in {"install_all", "automation_on_all", "automation_off_all"}:
                reason = {
                    "install_all": "массовое обновление LV-автоматики",
                    "automation_on_all": "массовый запуск LV-автоматики",
                    "automation_off_all": "массовая остановка LV-автоматики",
                }[name]
                for host in list((payload or {}).get("ok") or []):
                    coordinator.notify_mutation(str(host or ""), reason)
        except Exception:
            # Sheets is intentionally secondary; a sync scheduling failure must
            # never turn a successful RouterOS mutation into a failed operation.
            return

    VPNServersPage._on_action = patched

    # Install the final VPN page/search/event layer only after the standard UI
    # compatibility modules have already imported their mature components. This
    # avoids eager page imports from ui/__init__.py and keeps patch ordering
    # deterministic before MainWindow creates the actual page instances.
    from linkvideo_vpn_helper.ui.vpn_final_3_0_13_ux import install_vpn_final_3_0_13_ux
    install_vpn_final_3_0_13_ux()

    _INSTALLED = True
