from __future__ import annotations

"""Public bounded multi-VPN search facade for 3.0.8.

The existing login search already has a hard UI deadline.  This facade applies
the same rule to the auxiliary all-server operations so no future UI path can
reintroduce a wait-for-the-slowest-VPN hang.
"""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from linkvideo_vpn_helper.services.search_service_core import *  # noqa: F401,F403
from linkvideo_vpn_helper.services.search_service_core import FastSearchService as _CoreFastSearchService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


class FastSearchService(_CoreFastSearchService):
    def _bounded_all_server_calls(
        self,
        servers: list[str],
        creds: SessionCredentials,
        worker: Callable[[str], object],
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> tuple[list[tuple[str, object]], list[ServerSearchError], int]:
        """Run independent server calls without waiting for stale socket threads."""
        if not servers:
            return [], [], 0
        workers = min(self.max_workers, max(1, len(servers)))
        deadline = time.monotonic() + (
            float(deadline_seconds) if deadline_seconds is not None else self._deadline_seconds(creds, len(servers))
        )
        results: list[tuple[str, object]] = []
        errors: list[ServerSearchError] = []
        checked = 0
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpn-bounded-search")
        futures = {pool.submit(worker, server): server for server in servers}
        pending = set(futures)
        try:
            while pending:
                if cancel_event is not None and cancel_event.is_set():
                    for item in pending:
                        item.cancel()
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    server = futures[future]
                    checked += 1
                    try:
                        results.append((server, future.result()))
                    except Exception as exc:
                        errors.append(ServerSearchError(server, classify_exception(exc)))
                    if progress:
                        progress(checked, len(servers), server)

            for future in list(pending):
                server = futures[future]
                future.cancel()
                checked += 1
                errors.append(self._timeout_error(server))
                if progress:
                    progress(checked, len(servers), server)
            pending.clear()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return results, errors, checked

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
                client = self.vpn_service.get_client(server, creds, login, include_port_conflicts=True)
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
