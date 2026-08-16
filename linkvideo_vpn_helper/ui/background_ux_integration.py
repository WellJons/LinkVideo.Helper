from __future__ import annotations

import os
import queue
import subprocess
import threading
import time

from PySide6.QtCore import QTimer, Qt

from linkvideo_vpn_helper.services.errors import classify_exception


_INSTALLED = False


def _install_no_console_process_guard() -> None:
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


def _start_daemon_batch(hosts: list[str], worker, *, max_workers: int, thread_prefix: str):
    semaphore = threading.Semaphore(max(1, min(int(max_workers), max(1, len(hosts)))))
    output: queue.Queue[tuple[str, object | None, BaseException | None]] = queue.Queue()

    def run(host: str) -> None:
        with semaphore:
            try:
                output.put((host, worker(host), None))
            except BaseException as exc:
                output.put((host, None, exc))

    for host in hosts:
        threading.Thread(
            target=run,
            args=(host,),
            daemon=True,
            name=f"{thread_prefix}:{host}",
        ).start()
    return output


def _install_inactive_clients_auto_refresh() -> None:
    """Keep lifecycle data current without a blocking tab-entry scan."""
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
        self._busy_kind = "background-scan" if background else "scan"
        if not background:
            self._set_busy(True)
            self.task.show()
            self.task.busy("Проверяю VPN-серверы", f"Проверено 0 из {len(servers)}", 0)

        def one(server: str):
            return self.service.list_lifecycle_clients(
                server,
                self.credentials,
                self.INACTIVE_DAYS,
                True,
            )

        def worker():
            records = []
            errors = []
            pending = set(servers)
            completed = _start_daemon_batch(
                servers,
                one,
                max_workers=8,
                thread_prefix="inactive-vpn",
            )
            checked = 0
            deadline = time.monotonic() + (14.0 if background else 18.0)

            while pending and not cancel_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    server, payload, exc = completed.get(timeout=min(0.20, remaining))
                except queue.Empty:
                    continue
                if server not in pending:
                    continue
                pending.remove(server)
                checked += 1
                if exc is None:
                    records.extend(payload or [])
                else:
                    errors.append((server, classify_exception(exc)))
                if not background:
                    self.progressReady.emit(checked, len(servers), server)

            if cancel_event.is_set():
                return

            for server in servers:
                if server in pending:
                    errors.append(
                        (
                            server,
                            classify_exception(TimeoutError("VPN-сервер не завершил проверку до общего deadline")),
                        )
                    )
            if self._cancel_event is cancel_event:
                self.scanReady.emit(records, errors)

        threading.Thread(target=worker, daemon=True, name="inactive-vpn-scan").start()

    def _background_scan(self):
        if (
            self._lv_auto_refresh_running
            or getattr(self, "_cancel_event", None) is not None
            or (getattr(self, "_busy_kind", "") and getattr(self, "_busy_kind", "") != "background-scan")
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
        incoming = list(records or [])

        # The 1k+ lifecycle list is expensive to rebuild. If a minute refresh
        # returned byte-for-byte equivalent dataclass records, only finish the
        # background operation and keep the existing widgets intact.
        if was_background and incoming == list(getattr(self, "_records", [])):
            self._cancel_event = None
            self._busy_kind = ""
            self._lv_auto_refresh_running = False
            if errors:
                self.scan_summary.setText(
                    f"Найдено: {len(incoming)} · серверов с ошибкой: {len(errors)} · автообновление"
                )
            else:
                self.scan_summary.setText(f"Найдено учётных записей: {len(incoming)} · автообновление")
            return

        list_widget = getattr(self, "list", None)
        if list_widget is not None:
            list_widget.setUpdatesEnabled(False)
        try:
            original_on_scan(self, incoming, errors)
        finally:
            if list_widget is not None:
                list_widget.setUpdatesEnabled(True)
                list_widget.viewport().update()

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
        # Opening the page must never show a blocking scan dialog. Empty/stale
        # data are refreshed silently after the page is already interactive.
        if getattr(self, "_cancel_event", None) is None:
            QTimer.singleShot(350, lambda: _background_scan(self))

    def patched_deactivated(self):
        self._lv_refresh_timer.stop()
        if callable(original_deactivated):
            original_deactivated(self)

    def patched_refresh_servers(self):
        try:
            original_refresh_servers(self)
        finally:
            if self.isVisible() and getattr(self, "_cancel_event", None) is None:
                QTimer.singleShot(250, lambda: _background_scan(self))

    InactiveClientsPage.__init__ = patched_init
    InactiveClientsPage._scan = patched_scan
    InactiveClientsPage._on_scan = patched_on_scan
    InactiveClientsPage.onActivated = patched_activated
    InactiveClientsPage.onDeactivated = patched_deactivated
    InactiveClientsPage.refresh_servers = patched_refresh_servers
    InactiveClientsPage._lv_auto_refresh_installed = True


def _install_vpn_server_refresh_deadline() -> None:
    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    if getattr(VPNServersPage, "_lv_deadline_refresh_installed", False):
        return
    original_on_stats = VPNServersPage._on_stats

    def patched_refresh(self, silent: bool = False):
        if self._busy:
            return
        servers = self.registry.hosts()
        if not servers:
            self.task.show()
            self.task.warning("Нет VPN-серверов", "Включите серверы в настройках.")
            return

        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_busy(True)
        self._refresh_silent_mode = bool(silent)
        if not silent:
            self.task.show()
            self.task.busy("Проверяю VPN-серверы", f"Серверов: {len(servers)}")

        def one(host: str):
            stat = self.service.analyze_server_quick(host, self.credentials)
            auto = self.automation.get_status(host, self.credentials)
            return stat, auto

        def worker():
            rows = []
            pending = set(servers)
            completed = _start_daemon_batch(
                servers,
                one,
                max_workers=8,
                thread_prefix="vpn-dashboard",
            )
            deadline = time.monotonic() + 20.0
            while pending and not cancel_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    host, payload, exc = completed.get(timeout=min(0.20, remaining))
                except queue.Empty:
                    continue
                if host not in pending:
                    continue
                pending.remove(host)
                if exc is None:
                    stat, auto = payload
                    rows.append((host, stat, auto, None))
                else:
                    rows.append((host, None, None, classify_exception(exc).message))
            if cancel_event.is_set():
                return
            for host in servers:
                if host in pending:
                    rows.append((host, None, None, "Сервер не ответил до общего deadline"))
            if self._cancel_event is cancel_event:
                self.statsReady.emit(rows)

        threading.Thread(target=worker, daemon=True, name="vpn-dashboard-refresh").start()

    def patched_on_stats(self, rows):
        self._cancel_event = None
        return original_on_stats(self, rows)

    VPNServersPage.refresh = patched_refresh
    VPNServersPage._on_stats = patched_on_stats
    VPNServersPage._lv_deadline_refresh_installed = True


def install_background_ux() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_no_console_process_guard()
    _install_inactive_clients_auto_refresh()
    _install_vpn_server_refresh_deadline()
    _INSTALLED = True
