from __future__ import annotations

"""Schedule a Google Sheets reconciliation after LV automation mutations.

The normal five-minute reconciliation remains the safety net. This bridge makes
operator actions visible in Google Sheets within seconds, including immediate
quarantine/deletion performed when LV-Aging is enabled or updated.
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
    _INSTALLED = True
