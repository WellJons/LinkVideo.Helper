from __future__ import annotations

"""Ensure manual lifecycle scans visibly start before network work begins."""

from PySide6.QtCore import QTimer


_INSTALLED = False


def install_manual_scan_feedback() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.inactive_clients_page import InactiveClientsPage

    original_scan = InactiveClientsPage._scan

    def patched_scan(self):
        # The background auto-refresh integration has its own private scan path;
        # this class method is the user-triggered "Проверить серверы" action.
        if getattr(self, "_cancel_event", None) is not None:
            return
        servers = self.registry.hosts()
        if not servers:
            return original_scan(self)

        # Paint feedback first, then launch the real worker on the next event-loop
        # turn. Without this gap Windows could show the dialog only after the
        # first wave of RouterOS results, which looked like a frozen button.
        self.scan_summary.setText(f"Запускаю проверку {len(servers)} VPN-серверов…")
        self.task.show()
        self.task.busy("Проверяю VPN-серверы", f"Подготовка · 0 из {len(servers)}", 0)
        self.refresh_btn.setEnabled(False)

        def launch():
            # original_scan creates the cancellation event and updates the same
            # BusyDialog with real per-server progress.
            original_scan(self)

        QTimer.singleShot(90, launch)

    InactiveClientsPage._scan = patched_scan
    _INSTALLED = True
