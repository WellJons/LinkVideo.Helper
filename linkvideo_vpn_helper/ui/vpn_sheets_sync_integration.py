from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from linkvideo_vpn_helper.services.app_logging import event, error
from linkvideo_vpn_helper.services.vpn_sheets_sync import (
    GoogleSheetsBackend,
    SYNC_INTERVAL_SECONDS,
    VPNSheetsSyncService,
)
from linkvideo_vpn_helper.services.vpn_service import VPNService
from linkvideo_vpn_helper.ui.components import Card


_PATCHED_SERVICE = False
_PATCHED_PAGE = False
_COORDINATOR = None


class VPNSyncCoordinator(QObject):
    syncStarted = Signal(int)
    syncProgress = Signal(int, int, str, str)
    syncFinished = Signal(int, int)
    syncConfigMissing = Signal(str)
    mutationRequested = Signal(str, str, str)

    def __init__(self, vpn_service, credentials, registry, settings, parent=None):
        super().__init__(parent)
        self.vpn_service = vpn_service
        self.credentials = credentials
        self.registry = registry
        self.settings = settings
        self.backend = GoogleSheetsBackend.from_settings(settings)
        self.sync_service = VPNSheetsSyncService(vpn_service, self.backend) if self.backend else None
        self._busy = False
        self._host_inflight: set[str] = set()
        self._host_lock = threading.Lock()
        self._debounce: dict[str, QTimer] = {}
        self._mutation_reason: dict[str, tuple[str, str]] = {}
        self.last_failures: list[tuple[str, str]] = []
        self._periodic = QTimer(self)
        self._periodic.setInterval(SYNC_INTERVAL_SECONDS * 1000)
        self._periodic.timeout.connect(self._periodic_sync)
        self._periodic.start()
        self.mutationRequested.connect(self._queue_mutation)
        # Не создаём сетевую нагрузку прямо во время старта Helper.
        QTimer.singleShot(90_000, self._periodic_sync)
        if self.backend:
            email = str(getattr(self.backend, "service_account_info", {}).get("client_email", "") or "")
            event("SHEETS", "Google Sheets подключён", email)
        else:
            event("SHEETS", "Google Sheets не настроен")

    def is_configured(self) -> bool:
        return self.sync_service is not None

    @staticmethod
    def config_hint() -> str:
        return (
            "Ключ Google Sheets не найден. Используйте «Выбрать JSON» или поместите "
            "Service Account JSON в папку LinkVideo.Helper."
        )

    def _claim_host(self, server: str) -> bool:
        with self._host_lock:
            if server in self._host_inflight:
                return False
            self._host_inflight.add(server)
            return True

    def _release_host(self, server: str) -> None:
        with self._host_lock:
            self._host_inflight.discard(server)

    def notify_mutation(self, server: str, reason: str, login: str = "") -> None:
        self.mutationRequested.emit(str(server or ""), str(reason or "Изменение Helper"), str(login or ""))

    def _queue_mutation(self, server: str, reason: str, login: str):
        server = str(server or "").strip()
        if not server or not self.is_configured():
            return
        self._mutation_reason[server] = (reason, login)
        timer = self._debounce.get(server)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda h=server: self._sync_mutated_server(h))
            self._debounce[server] = timer
        # Дать RouterOS закончить цепочку и объединить batch-изменения в одну сверку.
        timer.start(1600)

    def _sync_mutated_server(self, server: str):
        if not self.is_configured():
            return
        reason, login = self._mutation_reason.pop(server, ("Изменение Helper", ""))
        source = f"Helper · {reason}"
        initiator = str(getattr(self.credentials, "username", "") or "LinkVideo.Helper")
        self._start_one(server, source=source, initiator=initiator)

    def _periodic_sync(self):
        if self._busy or not self.is_configured():
            return
        self.sync_all(manual=False)

    def _start_one(self, server: str, *, source: str, initiator: str):
        if not self.sync_service or not self._claim_host(server):
            return

        def worker():
            try:
                result = self.sync_service.sync_server(server, self.credentials, source=source, initiator=initiator)
                event(
                    "SHEETS",
                    "Синхронизирован сервер",
                    f"{server} · клиентов {result.clients} · +{result.added} · Δ{result.changed} · удалено {result.deleted} · восстановлено {result.restored}",
                )
            except Exception as exc:
                error("SHEETS", f"Ошибка синхронизации {server}", exc)
                # Автосинхронизация не должна мешать основной работе Helper.
            finally:
                self._release_host(server)

        threading.Thread(target=worker, daemon=True, name=f"sheets-sync-{server}").start()

    def sync_all(self, manual: bool = True) -> None:
        if self._busy:
            return
        if not self.sync_service:
            if manual:
                self.syncConfigMissing.emit(self.config_hint())
            return
        servers = list(self.registry.hosts())
        if not servers:
            if manual:
                self.syncFinished.emit(0, 0)
            return

        self._busy = True
        self.last_failures = []
        self.syncStarted.emit(len(servers))
        source = "Ручная синхронизация Helper" if manual else "Автосверка RouterOS"
        initiator = str(getattr(self.credentials, "username", "") or "LinkVideo.Helper") if manual else "LinkVideo.Helper auto-sync"
        event("SHEETS", "Начата сверка RouterOS → Google Sheets", f"серверов {len(servers)} · {'вручную' if manual else 'автоматически'}")

        def master():
            tasks: queue.Queue[str] = queue.Queue()
            results: queue.Queue[tuple[str, bool, str]] = queue.Queue()
            for host in servers:
                tasks.put(host)

            worker_count = min(4, len(servers))

            def runner():
                while True:
                    try:
                        host = tasks.get_nowait()
                    except queue.Empty:
                        return
                    if not self._claim_host(host):
                        results.put((host, False, "уже синхронизируется"))
                        tasks.task_done()
                        continue
                    try:
                        result = self.sync_service.sync_server(
                            host,
                            self.credentials,
                            source=source,
                            initiator=initiator,
                        )
                        detail = (
                            f"{result.clients} уч. · +{result.added} · Δ{result.changed} · "
                            f"удалено {result.deleted} · восстановлено {result.restored}"
                        )
                        results.put((host, True, detail))
                    except Exception as exc:
                        results.put((host, False, str(exc)[:240]))
                    finally:
                        self._release_host(host)
                        tasks.task_done()

            for index in range(worker_count):
                threading.Thread(
                    target=runner,
                    daemon=True,
                    name=f"sheets-sync-all-{index + 1}",
                ).start()

            done = ok = failed = 0
            deadline = time.monotonic() + 75.0
            seen: set[str] = set()
            while done < len(servers) and time.monotonic() < deadline:
                try:
                    host, success, detail = results.get(timeout=0.25)
                except queue.Empty:
                    continue
                if host in seen:
                    continue
                seen.add(host)
                done += 1
                ok += int(success)
                failed += int(not success)
                if success:
                    event("SHEETS", "Сверка сервера", f"{host} · {detail}")
                else:
                    self.last_failures.append((host, detail))
                    event("SHEETS", "Ошибка сверки сервера", f"{host} · {detail}", level=40)
                self.syncProgress.emit(done, len(servers), host, detail if success else f"Ошибка: {detail}")

            missing = [host for host in servers if host not in seen]
            for host in missing:
                done += 1
                failed += 1
                detail = "Тайм-аут общей синхронизации"
                self.last_failures.append((host, detail))
                event("SHEETS", "Ошибка сверки сервера", f"{host} · {detail}", level=40)
                self.syncProgress.emit(done, len(servers), host, detail)

            self._busy = False
            if failed:
                summary = "; ".join(f"{host}: {detail}" for host, detail in self.last_failures[:3])
                event("SHEETS", "Сверка завершена частично", f"успешно {ok} · ошибок {failed} · {summary}", level=30)
            else:
                event("SHEETS", "Сверка завершена", f"успешно {ok}")
            self.syncFinished.emit(ok, failed)

        threading.Thread(target=master, daemon=True, name="sheets-sync-master").start()


def _patch_vpn_service() -> None:
    global _PATCHED_SERVICE
    if _PATCHED_SERVICE:
        return

    method_names = {
        "create_clients_batch": "создание клиента",
        "add_ports": "добавление портов",
        "remove_port": "удаление порта",
        "set_password": "смена пароля",
        "set_secret_enabled": "включение/отключение учётки",
        "set_port_enabled": "включение/отключение порта",
        "recreate_port": "пересоздание NAT-порта",
        "delete_client": "удаление клиента",
    }

    for method_name, reason in method_names.items():
        original = getattr(VPNService, method_name)

        def make_wrapper(fn: Callable, event_reason: str, wrapped_name: str):
            def wrapper(self, *args, **kwargs):
                result = fn(self, *args, **kwargs)
                callback = getattr(self, "_vpn_sheets_change_callback", None)
                if callable(callback):
                    try:
                        server = str(args[0] if args else kwargs.get("server", "") or "")
                        login = ""
                        if wrapped_name == "create_clients_batch":
                            records = list(result or [])
                            login = ", ".join(str(getattr(item, "login", "") or "") for item in records if getattr(item, "login", ""))
                        elif len(args) >= 3:
                            login = str(args[2] or "")
                        else:
                            login = str(kwargs.get("login", "") or "")
                        callback(server, event_reason, login)
                    except Exception:
                        pass
                return result
            wrapper.__name__ = getattr(fn, "__name__", wrapped_name)
            wrapper.__doc__ = getattr(fn, "__doc__", None)
            return wrapper

        setattr(VPNService, method_name, make_wrapper(original, reason, method_name))

    _PATCHED_SERVICE = True


def _patch_vpn_servers_page() -> None:
    global _PATCHED_PAGE
    if _PATCHED_PAGE:
        return

    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    original_build = VPNServersPage._build

    def patched_build(self):
        original_build(self)
        coordinator = _COORDINATOR
        if coordinator is None:
            return

        card = Card(subtle=True)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(3)
        title = QLabel("База VPN-клиентов")
        title.setObjectName("Value")
        self.sheets_sync_status = QLabel()
        self.sheets_sync_status.setObjectName("TinyMuted")
        self.sheets_sync_status.setWordWrap(True)
        if coordinator.is_configured():
            self.sheets_sync_status.setText("Google Sheets · автосверка каждые 5 минут · ручные изменения RouterOS будут обнаружены")
        else:
            self.sheets_sync_status.setText("Google Sheets · ключ синхронизации ещё не установлен")
        text.addWidget(title)
        text.addWidget(self.sheets_sync_status)

        self.sheets_sync_btn = QPushButton("Синхронизировать")
        self.sheets_sync_btn.setProperty("role", "primary")
        self.sheets_sync_btn.clicked.connect(lambda: coordinator.sync_all(manual=True))
        layout.addLayout(text, 1)
        layout.addWidget(self.sheets_sync_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        # Header = 0, TaskStatus = 1. Карточка синхронизации идёт перед метриками.
        self.page_layout.insertWidget(2, card)

        def started(total: int):
            self.sheets_sync_btn.setEnabled(False)
            self.sheets_sync_btn.setText("Синхронизация 0/%d…" % total)
            self.sheets_sync_status.setText(f"Читаю RouterOS и сверяю базу · серверов {total}")

        def progress(done: int, total: int, host: str, detail: str):
            self.sheets_sync_btn.setText(f"Синхронизация {done}/{total}…")
            self.sheets_sync_status.setText(f"{host} · {detail}")

        def finished(ok: int, failed: int):
            self.sheets_sync_btn.setEnabled(True)
            self.sheets_sync_btn.setText("Синхронизировать")
            if failed:
                failures = list(getattr(coordinator, "last_failures", []) or [])
                if failures:
                    summary = "; ".join(f"{host}: {detail}" for host, detail in failures[:2])
                    extra = f" · ещё {len(failures) - 2}" if len(failures) > 2 else ""
                    self.sheets_sync_status.setText(
                        f"Сверка частичная · {ok}/{ok + failed} · {summary}{extra}"
                    )
                else:
                    self.sheets_sync_status.setText(f"Сверка завершена частично · успешно {ok} · ошибок {failed}")
            else:
                self.sheets_sync_status.setText(f"База синхронизирована · серверов {ok} · автосверка каждые 5 минут")

        def missing(message: str):
            self.sheets_sync_btn.setEnabled(True)
            self.sheets_sync_btn.setText("Синхронизировать")
            self.sheets_sync_status.setText(message)

        coordinator.syncStarted.connect(started)
        coordinator.syncProgress.connect(progress)
        coordinator.syncFinished.connect(finished)
        coordinator.syncConfigMissing.connect(missing)

    VPNServersPage._build = patched_build
    _PATCHED_PAGE = True


def attach_vpn_sheets_sync(window, vpn_service, credentials, settings):
    """Attach non-blocking Google Sheets reconciliation to the running Helper.

    RouterOS operations never depend on Google. A successful local mutation only
    schedules a background reconciliation; failed Sheets sync cannot roll back or
    block a client operation.
    """
    global _COORDINATOR
    _patch_vpn_service()
    coordinator = VPNSyncCoordinator(
        vpn_service,
        credentials,
        window.registry,
        settings,
        parent=window,
    )
    _COORDINATOR = coordinator
    vpn_service._vpn_sheets_change_callback = coordinator.notify_mutation
    window.vpn_sheets_sync = coordinator
    _patch_vpn_servers_page()
    return coordinator
