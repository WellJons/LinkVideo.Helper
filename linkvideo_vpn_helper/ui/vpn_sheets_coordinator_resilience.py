from __future__ import annotations

"""Conservative orchestration for RouterOS -> Google Sheets reconciliation.

Transport retries belong to ``vpn_sheets_resilience``. This layer prevents the UI
coordinator itself from amplifying a temporary Google slowdown: it performs one
Google preflight, uses only two server workers, removes the old 75-second global
cutoff, and opens a circuit after two fully retried Google failures.
"""

import queue
import threading
import time

from linkvideo_vpn_helper.services.app_logging import event, error
from linkvideo_vpn_helper.services.vpn_sheets_resilience import (
    friendly_google_error,
    is_transient_google_error,
)


_INSTALLED = False


def install_vpn_sheets_coordinator_resilience() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import linkvideo_vpn_helper.ui.vpn_sheets_sync_integration as integration

    coordinator_cls = integration.VPNSyncCoordinator

    def robust_start_one(self, server: str, *, source: str, initiator: str):
        if not self.sync_service or not self._claim_host(server):
            return

        def worker():
            try:
                for attempt in range(1, 3):
                    try:
                        result = self.sync_service.sync_server(
                            server,
                            self.credentials,
                            source=source,
                            initiator=initiator,
                        )
                    except Exception as exc:
                        if attempt == 1 and is_transient_google_error(exc):
                            event("SHEETS", "Повтор сверки сервера", f"{server} · через 3 с")
                            time.sleep(3.0)
                            continue
                        raise
                    else:
                        event(
                            "SHEETS",
                            "Синхронизирован сервер",
                            f"{server} · клиентов {result.clients} · +{result.added} · Δ{result.changed} · "
                            f"удалено {result.deleted} · восстановлено {result.restored}",
                        )
                        return
            except Exception as exc:
                error("SHEETS", f"Ошибка синхронизации {server}", RuntimeError(friendly_google_error(exc)))
            finally:
                self._release_host(server)

        threading.Thread(target=worker, daemon=True, name=f"sheets-sync-{server}").start()

    def robust_sync_all(self, manual: bool = True) -> None:
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
        initiator = (
            str(getattr(self.credentials, "username", "") or "LinkVideo.Helper")
            if manual else "LinkVideo.Helper auto-sync"
        )
        event(
            "SHEETS",
            "Начата сверка RouterOS → Google Sheets",
            f"серверов {len(servers)} · {'вручную' if manual else 'автоматически'}",
        )

        def master():
            # First prove that the actual server tabs are reachable and migrate
            # legacy grids in one request. LV Summary is deliberately excluded
            # from this gate because it is secondary and must never block the
            # authoritative per-server mirror.
            had_summary_cache = getattr(self.backend, "_lv_summary_rows", None)
            suppress_summary_preload = had_summary_cache is None
            if suppress_summary_preload:
                self.backend._lv_summary_rows = {}
            try:
                prepare = getattr(self.backend, "prepare_sync", None)
                if callable(prepare):
                    prepare(servers)
            except Exception as exc:
                detail = friendly_google_error(exc)
                self.last_failures = [(host, detail) for host in servers]
                for done, host in enumerate(servers, start=1):
                    self.syncProgress.emit(done, len(servers), host, f"Ошибка: {detail}")
                self._busy = False
                event("SHEETS", "Google preflight не пройден", detail, level=40)
                self.syncFinished.emit(0, len(servers))
                return
            finally:
                if suppress_summary_preload:
                    self.backend._lv_summary_rows = None

            tasks: queue.Queue[str] = queue.Queue()
            results: queue.Queue[tuple[str, bool, str, bool]] = queue.Queue()
            for host in servers:
                tasks.put(host)

            circuit_open = threading.Event()
            state_lock = threading.Lock()
            transient_failures = 0
            worker_count = min(2, len(servers))

            def runner():
                nonlocal transient_failures
                while True:
                    try:
                        host = tasks.get_nowait()
                    except queue.Empty:
                        return

                    if circuit_open.is_set():
                        results.put((
                            host,
                            False,
                            "Google Sheets временно недоступен — остальные серверы не запускались",
                            True,
                        ))
                        tasks.task_done()
                        continue

                    if not self._claim_host(host):
                        results.put((host, False, "сервер уже синхронизируется", False))
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
                        results.put((host, True, detail, False))
                    except Exception as exc:
                        transient = is_transient_google_error(exc)
                        detail = friendly_google_error(exc)
                        if transient:
                            with state_lock:
                                transient_failures += 1
                                # Each failure is already after the transport's
                                # own retries. Two independent servers failing is
                                # enough evidence to stop hammering Google.
                                if transient_failures >= 2:
                                    circuit_open.set()
                        results.put((host, False, detail, transient))
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
            while done < len(servers):
                host, success, detail, _transient = results.get()
                done += 1
                if success:
                    ok += 1
                    event("SHEETS", "Сверка сервера", f"{host} · {detail}")
                else:
                    failed += 1
                    self.last_failures.append((host, detail))
                    event("SHEETS", "Ошибка сверки сервера", f"{host} · {detail}", level=40)
                self.syncProgress.emit(
                    done,
                    len(servers),
                    host,
                    detail if success else f"Ошибка: {detail}",
                )

            self._busy = False
            if failed:
                summary = "; ".join(f"{host}: {detail}" for host, detail in self.last_failures[:3])
                event(
                    "SHEETS",
                    "Сверка завершена частично",
                    f"успешно {ok} · ошибок {failed} · {summary}",
                    level=30,
                )
            else:
                event("SHEETS", "Сверка завершена", f"успешно {ok}")
            self.syncFinished.emit(ok, failed)

        threading.Thread(target=master, daemon=True, name="sheets-sync-master").start()

    coordinator_cls._start_one = robust_start_one
    coordinator_cls.sync_all = robust_sync_all
    _INSTALLED = True
