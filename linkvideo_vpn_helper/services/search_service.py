from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
import time
from typing import Callable

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.errors import OperationError, classify_exception
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials, VPNService


@dataclass(slots=True)
class ServerSearchError:
    server: str
    error: OperationError


@dataclass(slots=True)
class SearchReport:
    matches: list[ClientRecord] = field(default_factory=list)
    errors: list[ServerSearchError] = field(default_factory=list)
    checked: int = 0
    total: int = 0


class FastSearchService:
    """Быстрый поиск: сначала лёгкий точечный запрос, полный snapshot только для совпадения."""

    def __init__(self, vpn_service: VPNService, max_workers: int = 8):
        self.vpn_service = vpn_service
        self.max_workers = max(2, int(max_workers))

    @staticmethod
    def _deadline_seconds(creds: SessionCredentials, server_count: int) -> float:
        """Hard UI-facing deadline for a multi-server search.

        RouterOS sockets have their own timeout, but a ThreadPoolExecutor used with
        ``as_completed`` can still keep the UI operation alive until the slowest
        worker finishes.  Search is an interactive operation, so after this hard
        deadline we return the results from healthy servers and mark the remaining
        servers as timed out.  The blocked socket threads are allowed to unwind in
        the background instead of holding the progress overlay forever.
        """
        per_socket = max(1.0, float(getattr(creds, "timeout", 4.5) or 4.5))
        # One batch for up to max_workers plus a little time for RouterOS login/print.
        # Keep the whole search comfortably below the point where it looks frozen.
        batches = max(1, (max(1, int(server_count)) + 7) // 8)
        return min(18.0, max(7.0, per_socket * min(2, batches) + 3.0))

    @staticmethod
    def _timeout_error(server: str) -> ServerSearchError:
        from linkvideo_vpn_helper.services.errors import ErrorKind
        return ServerSearchError(
            server,
            OperationError(
                ErrorKind.TIMEOUT,
                "Сервер не ответил вовремя; поиск продолжен без него",
                "hard multi-server search deadline exceeded",
            ),
        )

    @staticmethod
    def _print_exact(api: RouterOSAPIClient, path: str, field: str, value: str, proplist: str) -> list[dict]:
        params = {".proplist": proplist, f"?{field}=": value}
        try:
            return api.print(path, params)
        except Exception:
            # Некоторые старые RouterOS нестабильно работают с queries/proplist.
            rows = api.print(path)
            return [row for row in rows if str(row.get(field, "")).strip() == value]

    def search_exact_login(self, server: str, creds: SessionCredentials, login: str) -> SearchReport:
        """Exact one-server check used by the client-creation page.

        It deliberately does not treat login_1/login_2 as a collision with login.
        Login uniqueness is local to the selected MikroTik.
        """
        report = SearchReport(total=1)
        value = str(login or "").strip()
        if not value:
            return report
        report.checked = 1
        try:
            with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
                rows = self._print_exact(api, "/ppp/secret", "name", value, ".id,name")
            if rows:
                client = self.vpn_service.get_client(server, creds, value, include_port_conflicts=True)
                if client:
                    report.matches.append(client)
        except Exception as exc:
            report.errors.append(ServerSearchError(server, classify_exception(exc)))
        return report

    @staticmethod
    def _login_matches_query(name: str, query: str) -> bool:
        low = str(name or "").strip().lower()
        q = str(query or "").strip().lower()
        return bool(low and q and (low == q or low.startswith(q + "_")))

    def _server_matching_logins(self, server: str, creds: SessionCredentials, query: str) -> list[str]:
        """Возвращает точный логин и его серверные суффиксы (_1, _2, ...).

        В LinkVideo одинаковый базовый номер может существовать на разных VPN-серверах,
        а на одном сервере дополнительные записи получают суффикс. Поэтому поиск по
        899900000 должен также находить 899900000_1 на региональном сервере.
        Запрашиваем только поле name, без тяжёлого snapshot.
        """
        q = str(query or "").strip().lower()
        if not q:
            return []
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            try:
                rows = api.print("/ppp/secret", {".proplist": "name"})
            except Exception:
                rows = api.print("/ppp/secret")
        result: list[str] = []
        for row in rows:
            name = str(row.get("name", "") or "").strip()
            if self._login_matches_query(name, q):
                result.append(name)
        return result

    def search_login_all(
        self,
        servers: list[str],
        creds: SessionCredentials,
        query: str,
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> SearchReport:
        query = str(query or "").strip()
        report = SearchReport(total=len(servers))
        if not query:
            return report

        found: list[tuple[str, list[str]]] = []
        workers = min(self.max_workers, max(1, len(servers)))
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpn-search")
        futures = {pool.submit(self._server_matching_logins, server, creds, query): server for server in servers}
        pending = set(futures)
        hard_deadline = time.monotonic() + (
            float(deadline_seconds) if deadline_seconds is not None else self._deadline_seconds(creds, len(servers))
        )
        try:
            while pending:
                if cancel_event is not None and cancel_event.is_set():
                    for item in pending:
                        item.cancel()
                    return report
                remaining = hard_deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    server = futures[future]
                    report.checked += 1
                    try:
                        names = future.result()
                        if names:
                            found.append((server, names))
                    except Exception as exc:
                        report.errors.append(ServerSearchError(server, classify_exception(exc)))
                    if progress:
                        progress(report.checked, report.total, server)

            # Do not hold the UI open for non-responsive servers.  They are recorded
            # as partial failures and their sockets may finish in the background.
            if pending:
                timed_out = []
                for future in pending:
                    server = futures[future]
                    timed_out.append(server)
                    future.cancel()
                    report.checked += 1
                    report.errors.append(self._timeout_error(server))
                    if progress:
                        progress(report.checked, report.total, server)
                pending.clear()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        order = {server: index for index, server in enumerate(servers)}
        hydrate_jobs: list[tuple[str, str]] = []
        for server, names in sorted(found, key=lambda item: order.get(item[0], 10_000)):
            for name in names:
                hydrate_jobs.append((server, name))

        if not hydrate_jobs:
            return report

        # The discovery phase is complete at this point.  Client cards are hydrated
        # concurrently as a separate bounded phase so a stale matching server cannot
        # leave the progress overlay sitting at 100% forever.
        hydrate_workers = min(self.max_workers, max(1, len(hydrate_jobs)))
        hydrate_pool = ThreadPoolExecutor(max_workers=hydrate_workers, thread_name_prefix="vpn-search-detail")
        hydrate_futures = {
            hydrate_pool.submit(self.vpn_service.get_client, server, creds, name, True): (server, name)
            for server, name in hydrate_jobs
        }
        hydrate_pending = set(hydrate_futures)
        hydrate_budget = min(8.0, max(4.0, float(getattr(creds, "timeout", 4.5) or 4.5) + 1.5))
        if deadline_seconds is not None:
            hydrate_budget = min(hydrate_budget, max(0.05, float(deadline_seconds)))
        hydrate_deadline = time.monotonic() + hydrate_budget
        try:
            while hydrate_pending:
                if cancel_event is not None and cancel_event.is_set():
                    for item in hydrate_pending:
                        item.cancel()
                    return report
                remaining = hydrate_deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, hydrate_pending = wait(
                    hydrate_pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    server, _name = hydrate_futures[future]
                    try:
                        client = future.result()
                        if client and not any(x.server == client.server and x.login == client.login for x in report.matches):
                            report.matches.append(client)
                    except Exception as exc:
                        report.errors.append(ServerSearchError(server, classify_exception(exc)))
            for future in hydrate_pending:
                server, _name = hydrate_futures[future]
                future.cancel()
                report.errors.append(self._timeout_error(server))
        finally:
            hydrate_pool.shutdown(wait=False, cancel_futures=True)

        report.matches.sort(key=lambda client: (order.get(client.server, 10_000), client.login))
        return report

    def _server_logins(self, server: str, creds: SessionCredentials) -> set[str]:
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            try:
                rows = api.print("/ppp/secret", {".proplist": "name"})
            except Exception:
                rows = api.print("/ppp/secret")
        return {str(x.get("name", "") or "").strip() for x in rows if str(x.get("name", "") or "").strip()}

    def suggest_free_login_all(self, servers: list[str], creds: SessionCredentials, base_login: str, cancel_event=None) -> tuple[str, list[ServerSearchError]]:
        """Find a free login suffix across every reachable configured VPN server."""
        base = str(base_login or "").strip()
        if not base:
            return "", []
        existing: set[str] = set()
        errors: list[ServerSearchError] = []
        workers = min(self.max_workers, max(1, len(servers)))
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpn-login-suggest")
        futures = {pool.submit(self._server_logins, server, creds): server for server in servers}
        try:
            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for item in futures:
                        item.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    return "", errors
                server = futures[future]
                try:
                    existing.update(future.result())
                except Exception as exc:
                    errors.append(ServerSearchError(server, classify_exception(exc)))
        finally:
            if not (cancel_event is not None and cancel_event.is_set()):
                pool.shutdown(wait=True)
        if base not in existing:
            return base, errors
        index = 1
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return "", errors
            candidate = f"{base}_{index}"
            if candidate not in existing:
                return candidate, errors
            index += 1

    def _logins_for_remote(self, server: str, creds: SessionCredentials, remote: str) -> list[str]:
        """Находит PPP Secret по Remote Address без загрузки всех клиентов/conntrack."""
        remote = self.vpn_service._normalize_ip(remote)
        if not remote:
            return []
        names: list[str] = []
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            # Иногда remote-address задан прямо в Secret.
            try:
                secrets = api.print(
                    "/ppp/secret",
                    {".proplist": ".id,name,remote-address,profile", "?remote-address=": remote},
                )
            except Exception:
                secrets = api.print("/ppp/secret", {".proplist": ".id,name,remote-address,profile"})
            for row in secrets:
                if self.vpn_service._normalize_ip(row.get("remote-address", "")) == remote:
                    name = str(row.get("name", "") or "").strip()
                    if name and name not in names:
                        names.append(name)

            # В LinkVideo remote-address обычно хранится в PPP Profile, а Secret
            # ссылается на профиль по имени.
            try:
                profiles = api.print(
                    "/ppp/profile",
                    {".proplist": ".id,name,remote-address", "?remote-address=": remote},
                )
            except Exception:
                profiles = api.print("/ppp/profile", {".proplist": ".id,name,remote-address"})
            profile_names = [
                str(x.get("name", "") or "").strip()
                for x in profiles
                if self.vpn_service._normalize_ip(x.get("remote-address", "")) == remote
            ]
            for profile in profile_names:
                if not profile:
                    continue
                try:
                    rows = api.print(
                        "/ppp/secret",
                        {".proplist": ".id,name,profile", "?profile=": profile},
                    )
                except Exception:
                    rows = api.print("/ppp/secret", {".proplist": ".id,name,profile"})
                for row in rows:
                    if str(row.get("profile", "") or "").strip() != profile:
                        continue
                    name = str(row.get("name", "") or "").strip()
                    if name and name not in names:
                        names.append(name)
        return names

    def search_port(self, server: str, creds: SessionCredentials, port: int) -> SearchReport:
        report = SearchReport(total=1, checked=1)
        try:
            hints = self._server_port_hint(server, creds, int(port))
            login_hints = [x for x in hints if not x.startswith("@remote:")]
            remote_hints = [x.split(":", 1)[1] for x in hints if x.startswith("@remote:")]
            for remote in remote_hints:
                for name in self._logins_for_remote(server, creds, remote):
                    if name not in login_hints:
                        login_hints.append(name)
            for login in login_hints:
                client = self.vpn_service.get_client(server, creds, login, include_port_conflicts=True)
                if client and int(port) in client.ports and not any(x.login == client.login for x in report.matches):
                    report.matches.append(client)
        except Exception as exc:
            report.errors.append(ServerSearchError(server, classify_exception(exc)))
        return report

    def _server_port_hint(self, server: str, creds: SessionCredentials, port: int) -> list[str]:
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            try:
                rows = api.print("/ip/firewall/nat", {
                    ".proplist": ".id,dst-port,to-addresses,to-ports,comment,disabled",
                    "?dst-port=": str(port),
                })
            except Exception:
                rows = api.print("/ip/firewall/nat")
        logins = []
        remote = []
        for row in rows:
            ports = self.vpn_service._parse_ports(self.vpn_service._get_rule_external_port(row))
            if port not in ports:
                continue
            comment = str(row.get("comment", "") or "").strip()
            if comment and comment not in logins:
                logins.append(comment)
            address = self.vpn_service._normalize_ip(self.vpn_service._get_rule_remote(row))
            if address and address not in remote:
                remote.append(address)
        # Login hints are preferred; remote hints are encoded for fallback.
        return logins + ["@remote:" + x for x in remote]

    def search_port_all(self, servers: list[str], creds: SessionCredentials, port: int, progress: Callable[[int, int, str], None] | None = None) -> SearchReport:
        report = SearchReport(total=len(servers))
        if int(port or 0) <= 0:
            return report
        found: list[tuple[str, list[str]]] = []
        workers = min(self.max_workers, max(1, len(servers)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpn-port-search") as pool:
            futures = {pool.submit(self._server_port_hint, server, creds, int(port)): server for server in servers}
            for future in as_completed(futures):
                server = futures[future]
                report.checked += 1
                try:
                    hints = future.result()
                    if hints:
                        found.append((server, hints))
                except Exception as exc:
                    report.errors.append(ServerSearchError(server, classify_exception(exc)))
                if progress:
                    progress(report.checked, report.total, server)
        for server, hints in found:
            try:
                login_hints = [x for x in hints if not x.startswith("@remote:")]
                for login in login_hints:
                    client = self.vpn_service.get_client(server, creds, login, include_port_conflicts=True)
                    if client and int(port) in client.ports and not any(x.server == client.server and x.login == client.login for x in report.matches):
                        report.matches.append(client)
                remote_hints = [x.split(":", 1)[1] for x in hints if x.startswith("@remote:")]
                for remote in remote_hints:
                    for login in self._logins_for_remote(server, creds, remote):
                        if login in login_hints:
                            continue
                        client = self.vpn_service.get_client(server, creds, login, include_port_conflicts=True)
                        if client and int(port) in client.ports and not any(x.server == client.server and x.login == client.login for x in report.matches):
                            report.matches.append(client)
            except Exception as exc:
                report.errors.append(ServerSearchError(server, classify_exception(exc)))
        return report

    def _server_remote_hint(self, server: str, creds: SessionCredentials, remote: str) -> list[str]:
        return self._logins_for_remote(server, creds, remote)

    def search_remote_all(self, servers: list[str], creds: SessionCredentials, remote: str, progress: Callable[[int, int, str], None] | None = None) -> SearchReport:
        remote = self.vpn_service._normalize_ip(remote)
        report = SearchReport(total=len(servers))
        if not remote:
            return report
        found: list[tuple[str, list[str]]] = []
        workers = min(self.max_workers, max(1, len(servers)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpn-ip-search") as pool:
            futures = {pool.submit(self._server_remote_hint, server, creds, remote): server for server in servers}
            for future in as_completed(futures):
                server = futures[future]
                report.checked += 1
                try:
                    names = future.result()
                    if names:
                        found.append((server, names))
                except Exception as exc:
                    report.errors.append(ServerSearchError(server, classify_exception(exc)))
                if progress:
                    progress(report.checked, report.total, server)
        for server, names in found:
            for login in names:
                try:
                    client = self.vpn_service.get_client(server, creds, login, include_port_conflicts=True)
                    if client and client.remote_address == remote and not any(x.server == client.server and x.login == client.login for x in report.matches):
                        report.matches.append(client)
                except Exception as exc:
                    report.errors.append(ServerSearchError(server, classify_exception(exc)))
        return report

