from __future__ import annotations

"""Interactive multi-VPN search with a true UI deadline.

RouterOS calls are deliberately executed in daemon threads here. Python's
ThreadPoolExecutor uses non-daemon worker threads and a stuck socket can keep the
whole Helper process alive even after the UI operation has timed out. Search is
interactive, so a broken VPN must never own the application lifetime.
"""

import queue
import threading
import time
from typing import Callable, Iterable

from linkvideo_vpn_helper.services.search_service_core import *  # noqa: F401,F403
from linkvideo_vpn_helper.services.search_service_core import FastSearchService as _CoreFastSearchService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


class FastSearchService(_CoreFastSearchService):
    @staticmethod
    def _interactive_deadline_seconds(creds: SessionCredentials, server_count: int) -> float:
        """Total wall-clock budget for one interactive all-server operation."""
        socket_timeout = max(1.0, float(getattr(creds, "timeout", 4.5) or 4.5))
        return min(10.0, max(6.5, socket_timeout + 2.5))

    def _daemon_server_calls(
        self,
        servers: Iterable[str],
        worker: Callable[[str], object],
        *,
        creds: SessionCredentials,
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> tuple[list[tuple[str, object]], list[ServerSearchError], int]:
        """Run every VPN independently in daemon threads with one wall-clock budget."""
        ordered = [str(x).strip() for x in servers if str(x).strip()]
        total = len(ordered)
        if total == 0:
            return [], [], 0

        budget = (
            float(deadline_seconds)
            if deadline_seconds is not None
            else self._interactive_deadline_seconds(creds, total)
        )
        deadline_at = time.monotonic() + max(0.25, budget)
        completed: queue.Queue[tuple[str, bool, object]] = queue.Queue()

        def run_one(server: str) -> None:
            try:
                completed.put((server, True, worker(server)))
            except BaseException as exc:
                completed.put((server, False, exc))

        for server in ordered:
            threading.Thread(
                target=run_one,
                args=(server,),
                daemon=True,
                name=f"lv-vpn-search:{server}",
            ).start()

        pending = set(ordered)
        results: list[tuple[str, object]] = []
        errors: list[ServerSearchError] = []
        checked = 0

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                break
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                break
            try:
                server, ok, payload = completed.get(timeout=min(0.12, remaining))
            except queue.Empty:
                continue
            if server not in pending:
                continue
            pending.remove(server)
            checked += 1
            if ok:
                results.append((server, payload))
            else:
                errors.append(ServerSearchError(server, classify_exception(payload)))
            if progress and not (cancel_event is not None and cancel_event.is_set()):
                progress(checked, total, server)

        if cancel_event is None or not cancel_event.is_set():
            for server in ordered:
                if server not in pending:
                    continue
                pending.remove(server)
                checked += 1
                errors.append(self._timeout_error(server))
                if progress:
                    progress(checked, total, server)

        return results, errors, checked

    def search_login_all(
        self,
        servers: list[str],
        creds: SessionCredentials,
        query: str,
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> SearchReport:
        """Search and hydrate each VPN inside the same bounded per-server task."""
        value = str(query or "").strip()
        report = SearchReport(total=len(servers))
        if not value:
            return report

        def search_one(server: str) -> list:
            matches = []
            for login in self._server_matching_logins(server, creds, value):
                if cancel_event is not None and cancel_event.is_set():
                    break
                # Conflict metadata is part of the search-card contract; keep the
                # named flag explicit so it cannot silently regress.
                client = self.vpn_service.get_client(
                    server,
                    creds,
                    login,
                    include_port_conflicts=True,
                )
                if client is not None:
                    matches.append(client)
            return matches

        results, errors, checked = self._daemon_server_calls(
            servers,
            search_one,
            creds=creds,
            progress=progress,
            cancel_event=cancel_event,
            deadline_seconds=deadline_seconds,
        )
        report.checked = checked
        report.errors.extend(errors)

        order = {server: index for index, server in enumerate(servers)}
        for _server, clients in results:
            for client in clients or []:
                if not any(x.server == client.server and x.login == client.login for x in report.matches):
                    report.matches.append(client)
        report.matches.sort(key=lambda client: (order.get(client.server, 10_000), client.login))
        return report

    def _bounded_all_server_calls(
        self,
        servers: list[str],
        creds: SessionCredentials,
        worker: Callable[[str], object],
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> tuple[list[tuple[str, object]], list[ServerSearchError], int]:
        return self._daemon_server_calls(
            servers,
            worker,
            creds=creds,
            progress=progress,
            cancel_event=cancel_event,
            deadline_seconds=deadline_seconds,
        )

    def suggest_free_login_all(
        self,
        servers: list[str],
        creds: SessionCredentials,
        base_login: str,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> tuple[str, list[ServerSearchError]]:
        base = str(base_login or "").strip()
        if not base:
            return "", []
        results, errors, _checked = self._bounded_all_server_calls(
            servers,
            creds,
            lambda server: self._server_logins(server, creds),
            cancel_event=cancel_event,
            deadline_seconds=deadline_seconds,
        )
        if cancel_event is not None and cancel_event.is_set():
            return "", errors
        existing: set[str] = set()
        for _server, names in results:
            existing.update(names or set())
        if base not in existing:
            return base, errors
        index = 1
        while True:
            candidate = f"{base}_{index}"
            if candidate not in existing:
                return candidate, errors
            index += 1

    def search_port_all(
        self,
        servers: list[str],
        creds: SessionCredentials,
        port: int,
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> SearchReport:
        report = SearchReport(total=len(servers))
        value = int(port or 0)
        if value <= 0:
            return report
        results, errors, checked = self._bounded_all_server_calls(
            servers,
            creds,
            lambda server: self.search_port(server, creds, value),
            progress=progress,
            cancel_event=cancel_event,
            deadline_seconds=deadline_seconds,
        )
        report.checked = checked
        report.errors.extend(errors)
        for _server, partial in results:
            if not isinstance(partial, SearchReport):
                continue
            report.errors.extend(partial.errors)
            for client in partial.matches:
                if not any(x.server == client.server and x.login == client.login for x in report.matches):
                    report.matches.append(client)
        return report

    def _search_remote_one(self, server: str, creds: SessionCredentials, remote: str) -> SearchReport:
        report = SearchReport(total=1, checked=1)
        try:
            for login in self._server_remote_hint(server, creds, remote):
                client = self.vpn_service.get_client(
                    server,
                    creds,
                    login,
                    include_port_conflicts=True,
                )
                if client and client.remote_address == remote:
                    if not any(x.login == client.login for x in report.matches):
                        report.matches.append(client)
        except Exception as exc:
            report.errors.append(ServerSearchError(server, classify_exception(exc)))
        return report

    def search_remote_all(
        self,
        servers: list[str],
        creds: SessionCredentials,
        remote: str,
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> SearchReport:
        remote = self.vpn_service._normalize_ip(remote)
        report = SearchReport(total=len(servers))
        if not remote:
            return report
        results, errors, checked = self._bounded_all_server_calls(
            servers,
            creds,
            lambda server: self._search_remote_one(server, creds, remote),
            progress=progress,
            cancel_event=cancel_event,
            deadline_seconds=deadline_seconds,
        )
        report.checked = checked
        report.errors.extend(errors)
        for _server, partial in results:
            if not isinstance(partial, SearchReport):
                continue
            report.errors.extend(partial.errors)
            for client in partial.matches:
                if not any(x.server == client.server and x.login == client.login for x in report.matches):
                    report.matches.append(client)
        return report
