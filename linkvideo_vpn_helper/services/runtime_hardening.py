from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from linkvideo_vpn_helper.services.errors import OperationCancelled, classify_exception


_INSTALLED = False


def install_service_runtime_hardening() -> None:
    """Remove remaining all-server executor waits from interactive workflows.

    Search/dashboard/lifecycle scans already have their own hard deadlines. New
    client creation also probes every VPN server to pick the least loaded host;
    historically that method used ``with ThreadPoolExecutor`` + ``as_completed``
    and therefore waited for every running socket during context-manager exit.
    One broken VPN could hold the create-client workflow even after cancellation.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services.vpn_service import (
        VPNService,
        VPN_L2TP_SOFT_LIMIT,
    )

    if getattr(VPNService, "_lv_server_selection_deadline_installed", False):
        _INSTALLED = True
        return

    def pick_best_server_parallel(self, servers, creds, max_workers=6, cancel_event=None):
        servers = list(servers or [])
        if not servers:
            raise ValueError("В списке нет активных VPN-серверов")

        if cancel_event is None:
            cancel_event = threading.Event()

        best_server = None
        best_score = None
        failures: list[tuple[str, str]] = []
        workers = min(max(1, int(max_workers)), max(1, len(servers)))

        # All hosts are probed concurrently in a few bounded waves. The RouterOS
        # socket itself has a timeout, but this outer wall-clock deadline protects
        # the UI even if a platform/network stack ignores one of those waits.
        per_socket = max(1.0, float(getattr(creds, "timeout", 4.5) or 4.5))
        waves = (len(servers) + workers - 1) // workers
        deadline_seconds = min(24.0, max(8.0, per_socket * min(waves, 2) + 5.0))
        deadline = time.monotonic() + deadline_seconds

        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpn-probe")
        futures = {pool.submit(self.analyze_server_quick, server, creds): server for server in servers}
        pending = set(futures)
        try:
            while pending and not cancel_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.35, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    server = futures[future]
                    try:
                        stat = future.result()
                    except Exception as exc:
                        failures.append((server, classify_exception(exc).message))
                        continue

                    unknown_metrics = int(stat.cpu_load is None) + int(stat.memory_usage_percent is None)
                    if stat.clients_online >= VPN_L2TP_SOFT_LIMIT:
                        failures.append(
                            (
                                server,
                                f"{stat.clients_online} активных L2TP — достигнут резервный порог {VPN_L2TP_SOFT_LIMIT}",
                            )
                        )
                        continue
                    score = (
                        stat.clients_online,
                        unknown_metrics,
                        stat.cpu_load if stat.cpu_load is not None else 10_000,
                        stat.memory_usage_percent if stat.memory_usage_percent is not None else 10_000,
                        stat.ports_total,
                        stat.clients_total,
                    )
                    if best_score is None or score < best_score:
                        best_score = score
                        best_server = stat.server

            if cancel_event.is_set():
                for future in pending:
                    future.cancel()
                raise OperationCancelled("Операция отменена")

            for future in pending:
                server = futures[future]
                future.cancel()
                failures.append((server, f"сервер не завершил проверку за {deadline_seconds:.0f} сек"))
        finally:
            # Never wait for a socket that has already exceeded the user-visible
            # workflow deadline. Running daemon/executor threads unwind on their
            # own RouterOS socket timeout without holding the Qt completion path.
            pool.shutdown(wait=False, cancel_futures=True)

        if best_server:
            return best_server

        detail = "; ".join(f"{host}: {reason}" for host, reason in failures[:5])
        if len(failures) > 5:
            detail += f"; ещё {len(failures) - 5}"
        capacity_failures = [reason for _host, reason in failures if "активных L2TP" in reason]
        if capacity_failures and len(capacity_failures) == len(failures):
            raise RuntimeError(
                f"Нет VPN-сервера ниже безопасного порога {VPN_L2TP_SOFT_LIMIT} активных L2TP. "
                "Нового клиента автоматически создавать нельзя." + ((" " + detail) if detail else "")
            )
        raise RuntimeError("Не удалось подобрать доступный VPN-сервер" + ((". " + detail) if detail else ""))

    VPNService.pick_best_server_parallel = pick_best_server_parallel
    VPNService._lv_server_selection_deadline_installed = True
    _INSTALLED = True
