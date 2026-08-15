from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from PySide6.QtCore import QTimer, Qt

from linkvideo_vpn_helper.services.errors import classify_exception


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
    """Keep lifecycle data current and make its all-server scan deadline-bounded.

    The first visit performs the normal visible scan. Later refreshes run every
    minute without opening a modal spinner over the operator. Both manual and
    automatic scans have a hard deadline and abandon stuck RouterOS futures
    without waiting for executor shutdown.
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
        self._lv_refresh_timer = QTimer(self)
        self._lv_refresh_timer.setInterval(60_000)
        self._lv_refresh_timer.timeout.connect(lambda: _background_scan(self))

    def patched_scan(self):
        servers = self.registry.hosts()
        background = bool(getattr(self, "_lv_auto_refresh_running", False))
        if not servers:
            if background:
                self._lv_auto_refresh_running = False
                return
            self.task.show()
            self.task.warning("Нет активных VPN-серверов", "Включите серверы в настройках.")
            return
        if getattr(self, "_cancel_event", None) is not None:
            return

        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._busy_kind = "scan"
        self._set_busy(True)
        if not background:
            self.task.show()
            self.task.busy("Проверяю VPN-серверы", f"Проверено 0 из {len(servers)}", 0)

        def worker():
            records = []
            errors = []
            workers = min(8, max(1, len(servers)))
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="inactive-vpn")
            futures = {
                pool.submit(
                    self.service.list_lifecycle_clients,
                    server,
                    self.credentials,
                    self.INACTIVE_DAYS,
                    True,
                ): server
                for server in servers
            }
            pending = set(futures)
            checked = 0
            deadline = time.monotonic() + 24.0
            try:
                while pending and not cancel_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    done, pending = wait(
                        pending,
                        timeout=min(0.4, remaining),
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        server = futures[future]
                        checked += 1
                        try:
                            records.extend(future.result())
                        except Exception as exc:
                            errors.append((server, classify_exception(exc)))
                        if not background:
                            self.progressReady.emit(checked, len(servers), server)

                if cancel_event.is_set():
                    for future in pending:
                        future.cancel()
                    return

                if pending:
                    for future in pending:
                        server = futures[future]
                        future.cancel()
                        errors.append(
                            (
                                server,
                                classify_exception(
                                    TimeoutError("VPN-сервер не завершил проверку до общего deadline")
                                ),
                            )
                        )
                    checked += len(pending)
            finally:
                # Critical: never wait here for a broken socket/thread. Its
                # result is no longer allowed to hold the Qt completion path.
                pool.shutdown(wait=False, cancel_futures=True)

            if not cancel_event.is_set() and self._cancel_event is cancel_event:
                self.scanReady.emit(records, errors)

        threading.Thread(target=worker, daemon=True, name="inactive-vpn-scan").start()

    def _background_scan(self):
        if (
            self._lv_auto_refresh_running
            or getattr(self, "_cancel_event", None) is not None
            or getattr(self, "_busy_kind", "")
            or not self.isVisible()
            or not self.registry.hosts()
        ):
            return
        self._lv_auto_refresh_running = True
        self._lv_auto_refresh_selection = set(getattr(self, "_selected_keys", set()))
        current = getattr(self, "current", None)
        self._lv_auto_refresh_current = self._key(current) if current is not None else None
        patched_scan(self)

    def patched_on_scan(self, records, errors):
        was_background = bool(getattr(self, "_lv_auto_refresh_running", False))
        old_selection = set(getattr(self, "_lv_auto_refresh_selection", set()))
        old_current = getattr(self, "_lv_auto_refresh_current", None)
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
        self._sync_card_states()
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
    InactiveClientsPage._scan = patched_scan
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
