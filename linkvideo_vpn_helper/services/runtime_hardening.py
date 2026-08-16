from __future__ import annotations

import queue
import threading
import time

from linkvideo_vpn_helper.services.errors import OperationCancelled, classify_exception


_INSTALLED = False


def install_service_runtime_hardening() -> None:
    """Remove the remaining process-owning all-server executor from UI work.

    Interactive search already uses daemon workers because ThreadPoolExecutor
    workers are non-daemon and may keep the process alive after a UI deadline.
    Apply the same rule to automatic least-loaded VPN selection used by client
    creation: bounded concurrency, one wall-clock deadline, daemon threads only.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services.vpn_service import VPNService, VPN_L2TP_SOFT_LIMIT

    if getattr(VPNService, "_lv_server_selection_deadline_installed", False):
        _INSTALLED = True
        return

    def pick_best_server_parallel(self, servers, creds, max_workers=6, cancel_event=None):
        servers = [str(x).strip() for x in (servers or []) if str(x).strip()]
        if not servers:
            raise ValueError("В списке нет активных VPN-серверов")

        event = cancel_event or threading.Event()
        workers = min(max(1, int(max_workers)), len(servers))
        semaphore = threading.Semaphore(workers)
        completed: queue.Queue[tuple[str, object | None, BaseException | None]] = queue.Queue()

        per_socket = max(1.0, float(getattr(creds, "timeout", 4.5) or 4.5))
        waves = (len(servers) + workers - 1) // workers
        deadline_seconds = min(24.0, max(8.0, per_socket * min(waves, 2) + 5.0))
        deadline = time.monotonic() + deadline_seconds

        def run_one(server: str) -> None:
            # A thread waiting on this semaphore is daemon too and therefore can
            # never keep Helper alive during shutdown.
            with semaphore:
                if event.is_set():
                    return
                try:
                    completed.put((server, self.analyze_server_quick(server, creds), None))
                except BaseException as exc:
                    completed.put((server, None, exc))

        for server in servers:
            threading.Thread(
                target=run_one,
                args=(server,),
                daemon=True,
                name=f"lv-vpn-probe:{server}",
            ).start()

        pending = set(servers)
        best_server = None
        best_score = None
        failures: list[tuple[str, str]] = []

        while pending and not event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                server, stat, exc = completed.get(timeout=min(0.20, remaining))
            except queue.Empty:
                continue
            if server not in pending:
                continue
            pending.remove(server)

            if exc is not None:
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

        if event.is_set():
            raise OperationCancelled("Операция отменена")

        for server in servers:
            if server in pending:
                failures.append((server, f"сервер не завершил проверку за {deadline_seconds:.0f} сек"))

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
