from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QTimer, Qt


_INSTALLED = False


def _install_no_console_process_guard() -> None:
    """Prevent console helpers (FFmpeg/PowerShell/taskkill/etc.) flashing windows.

    Runtime code is GUI-only. On Windows every subprocess inherits
    CREATE_NO_WINDOW unless a caller explicitly requests CREATE_NEW_CONSOLE.
    subprocess.run/check_output use subprocess.Popen internally, so one guard
    also covers helper paths that are easy to miss during future development.
    """
    if os.name != "nt" or getattr(subprocess.Popen, "_lv_no_console_guard", False):
        return

    original = subprocess.Popen
    no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    new_console = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010))

    class HiddenPopen(original):
        _lv_no_console_guard = True

        def __init__(self, *args, **kwargs):
            flags = int(kwargs.get("creationflags", 0) or 0)
            if not flags & new_console:
                kwargs["creationflags"] = flags | no_window
            super().__init__(*args, **kwargs)

    subprocess.Popen = HiddenPopen


def _install_inactive_clients_auto_refresh() -> None:
    """Keep lifecycle data current without throwing a modal over the operator.

    The first visit performs the normal visible scan. Later refreshes run every
    minute using the existing bounded worker path while suppressing only the
    BusyDialog. Actions stay disabled during the refresh, so a background scan
    cannot race a disable/delete operation.
    """
    from linkvideo_vpn_helper.ui.pages.inactive_clients_page import InactiveClientsPage

    if getattr(InactiveClientsPage, "_lv_auto_refresh_installed", False):
        return

    original_init = InactiveClientsPage.__init__
    original_on_scan = InactiveClientsPage._on_scan
    original_refresh_servers = InactiveClientsPage.refresh_servers
    original_activated = getattr(InactiveClientsPage, "onActivated", None)
    original_deactivated = getattr(InactiveClientsPage, "onDeactivated", None)

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._lv_auto_refresh_running = False
        self._lv_auto_refresh_selection = set()
        self._lv_auto_refresh_current = None
        self._lv_task_busy_original = None
        self._lv_task_show_original = None
        self._lv_refresh_timer = QTimer(self)
        self._lv_refresh_timer.setInterval(60_000)
        self._lv_refresh_timer.timeout.connect(lambda: _background_scan(self))

    def _background_scan(self):
        if (
            self._lv_auto_refresh_running
            or getattr(self, "_cancel_event", None) is not None
            or getattr(self, "_busy_kind", "")
            or not self.isVisible()
        ):
            return
        self._lv_auto_refresh_running = True
        self._lv_auto_refresh_selection = set(getattr(self, "_selected_keys", set()))
        current = getattr(self, "current", None)
        self._lv_auto_refresh_current = self._key(current) if current is not None else None

        # Reuse the proven scan/cancel implementation, but do not open a modal
        # every 60 seconds. Restore these methods as soon as scanReady arrives.
        self._lv_task_busy_original = self.task.busy
        self._lv_task_show_original = self.task.show
        self.task.busy = lambda *args, **kwargs: None
        self.task.show = lambda *args, **kwargs: None
        try:
            self._scan()
        except Exception:
            _restore_task(self)
            self._lv_auto_refresh_running = False
            raise

    def _restore_task(self):
        busy = getattr(self, "_lv_task_busy_original", None)
        show = getattr(self, "_lv_task_show_original", None)
        if busy is not None:
            self.task.busy = busy
        if show is not None:
            self.task.show = show
        self._lv_task_busy_original = None
        self._lv_task_show_original = None

    def patched_on_scan(self, records, errors):
        was_background = bool(getattr(self, "_lv_auto_refresh_running", False))
        old_selection = set(getattr(self, "_lv_auto_refresh_selection", set()))
        old_current = getattr(self, "_lv_auto_refresh_current", None)
        if was_background:
            _restore_task(self)
        original_on_scan(self, records, errors)
        if not was_background:
            return

        self._lv_auto_refresh_running = False
        valid = {self._key(record) for record in getattr(self, "_records", [])}
        self._selected_keys = old_selection & valid
        if old_current in valid:
            for row in range(self.list.count()):
                item = self.list.item(row)
                record = item.data(Qt.ItemDataRole.UserRole)
                if record is not None and self._key(record) == old_current:
                    self.list.setCurrentRow(row)
                    self.current = record
                    self._sync_card_states()
                    self._render_detail()
                    break
        self._sync_batch_controls()

    def patched_activated(self):
        if callable(original_activated):
            original_activated(self)
        self._lv_refresh_timer.start()
        if not getattr(self, "_records", None) and getattr(self, "_cancel_event", None) is None:
            QTimer.singleShot(180, self._scan)

    def patched_deactivated(self):
        self._lv_refresh_timer.stop()
        if callable(original_deactivated):
            original_deactivated(self)

    def patched_refresh_servers(self):
        try:
            original_refresh_servers(self)
        finally:
            if self.isVisible() and getattr(self, "_cancel_event", None) is None:
                QTimer.singleShot(120, lambda: _background_scan(self))

    InactiveClientsPage.__init__ = patched_init
    InactiveClientsPage._on_scan = patched_on_scan
    InactiveClientsPage.onActivated = patched_activated
    InactiveClientsPage.onDeactivated = patched_deactivated
    InactiveClientsPage.refresh_servers = patched_refresh_servers
    InactiveClientsPage._lv_auto_refresh_installed = True


def install_background_ux() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_no_console_process_guard()
    _install_inactive_clients_auto_refresh()
    _INSTALLED = True
