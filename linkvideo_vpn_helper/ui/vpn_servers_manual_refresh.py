from __future__ import annotations

"""Do not poll every VPN server merely because the operator opened the tab."""


_INSTALLED = False


def install_vpn_servers_manual_refresh() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from PySide6.QtWidgets import QWidget
    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    def show_event(self, event):
        QWidget.showEvent(self, event)
        timer = getattr(self, "_refresh_timer", None)
        if timer is not None:
            timer.stop()

    def on_activated(self):
        timer = getattr(self, "_refresh_timer", None)
        if timer is not None:
            timer.stop()
        if getattr(self, "table", None) is not None and self.table.rowCount() == 0:
            self.summary.setText("Нажмите «Обновить данные», чтобы проверить VPN-серверы.")

    def on_deactivated(self):
        timer = getattr(self, "_refresh_timer", None)
        if timer is not None:
            timer.stop()

    def refresh_servers(self):
        # Registry/settings changed. Refresh is intentionally operator-triggered;
        # opening/switching tabs must not fan out API calls to every RouterOS.
        if getattr(self, "summary", None) is not None:
            self.summary.setText("Список серверов изменён. Нажмите «Обновить данные» для проверки.")

    VPNServersPage.showEvent = show_event
    VPNServersPage.onActivated = on_activated
    VPNServersPage.onDeactivated = on_deactivated
    VPNServersPage.refresh_servers = refresh_servers
    _INSTALLED = True
