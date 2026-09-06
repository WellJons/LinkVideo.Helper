from __future__ import annotations

import threading
import types

from linkvideo_vpn_helper.services.app_logging import event
from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient
from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.search_service_core import SearchReport, ServerSearchError
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, PortConflict


_INSTALLED = False


def _record(row: dict) -> ClientRecord:
    conflicts: dict[int, list[PortConflict]] = {}
    for raw_port, owners in dict(row.get("port_conflicts") or {}).items():
        try:
            port = int(raw_port)
        except Exception:
            continue
        parsed: list[PortConflict] = []
        for item in list(owners or []):
            parsed.append(PortConflict(
                port=port,
                rule_id=str(item.get("rule_id") or ""),
                owner_login=str(item.get("owner_login") or ""),
                owner_remote_address=str(item.get("owner_remote_address") or ""),
                owner_comment=str(item.get("owner_comment") or ""),
                disabled=bool(item.get("disabled")),
            ))
        if parsed:
            conflicts[port] = parsed

    def ints(value) -> list[int]:
        result: list[int] = []
        for item in list(value or []):
            try:
                port = int(item)
            except Exception:
                continue
            if 1 <= port <= 65535 and port not in result:
                result.append(port)
        return sorted(result)

    return ClientRecord(
        server=str(row.get("server") or ""),
        login=str(row.get("login") or ""),
        password=str(row.get("password") or ""),
        remote_address=str(row.get("remote_address") or ""),
        ports=ints(row.get("ports")),
        nat_rule_ids=[str(item) for item in list(row.get("nat_rule_ids") or []) if str(item or "")],
        is_online=bool(row.get("is_online")),
        active_ports=ints(row.get("active_ports")),
        disabled_ports=ints(row.get("disabled_ports")),
        is_enabled=bool(row.get("is_enabled", True)),
        last_logged_out=str(row.get("last_logged_out") or ""),
        uptime=str(row.get("uptime") or ""),
        port_conflicts=conflicts,
    )


def install_cloud_search_bridge(search_service, settings, vpn_service=None) -> None:
    """Route interactive client search to PostgreSQL when VPNSync is configured."""
    global _INSTALLED
    if _INSTALLED:
        return

    cloud = getattr(vpn_service, "_cloud_vpnsync_client", None) if vpn_service is not None else None
    if not isinstance(cloud, CloudVPNSyncClient):
        cloud = CloudVPNSyncClient(settings)
    lock = getattr(vpn_service, "_cloud_activity_lock", None) if vpn_service is not None else None
    if lock is None:
        lock = threading.RLock()

    originals = {
        name: getattr(search_service, name)
        for name in (
            "search_login_all",
            "search_port_all",
            "search_remote_all",
            "search_exact_login",
            "suggest_free_login_all",
        )
        if callable(getattr(search_service, name, None))
    }

    def configured() -> bool:
        fresh = cloud.store.load()
        ready = bool(
            str(fresh.host or "").strip()
            and str(fresh.username or "").strip()
            and str(fresh.password or "")
        )
        if ready:
            current = cloud.config
            if (
                fresh.host != current.host
                or int(fresh.port) != int(current.port)
                or fresh.username != current.username
                or fresh.password != current.password
                or bool(fresh.use_tls) != bool(current.use_tls)
            ):
                cloud.set_config(fresh, save=False)
        return ready

    def fetch(query: str, limit: int = 200) -> list[ClientRecord]:
        with lock:
            rows = cloud.search_clients(str(query or ""), limit=limit)
        return [_record(dict(row or {})) for row in rows]

    def report_for(servers: list[str], query: str, progress=None, cancel_event=None) -> SearchReport:
        report = SearchReport(total=len(servers))
        if cancel_event is not None and cancel_event.is_set():
            return report
        allowed = {str(server or "").strip().lower() for server in servers if str(server or "").strip()}
        try:
            records = fetch(query)
        except Exception as exc:
            report.errors.append(ServerSearchError("VPNSync", classify_exception(exc)))
            return report
        report.matches = [
            record for record in records
            if not allowed or record.server.lower() in allowed
        ]
        report.checked = len(servers)
        if progress:
            for index, server in enumerate(servers, 1):
                try:
                    progress(index, len(servers), server)
                except Exception as exc:
                    event(
                        "CLOUD",
                        "Callback прогресса поиска завершился ошибкой",
                        f"{type(exc).__name__}: {exc}",
                        level=30,
                    )
                    break
        return report

    def search_login_all(self, servers, creds, query, progress=None, cancel_event=None, deadline_seconds=None):
        if not configured():
            return originals["search_login_all"](
                servers, creds, query, progress=progress,
                cancel_event=cancel_event, deadline_seconds=deadline_seconds,
            )
        return report_for(list(servers), str(query or ""), progress, cancel_event)

    def search_port_all(self, servers, creds, port, progress=None, cancel_event=None, deadline_seconds=None):
        if not configured():
            return originals["search_port_all"](
                servers, creds, port, progress=progress,
                cancel_event=cancel_event, deadline_seconds=deadline_seconds,
            )
        return report_for(list(servers), str(int(port or 0)), progress, cancel_event)

    def search_remote_all(self, servers, creds, remote, progress=None, cancel_event=None, deadline_seconds=None):
        if not configured():
            return originals["search_remote_all"](
                servers, creds, remote, progress=progress,
                cancel_event=cancel_event, deadline_seconds=deadline_seconds,
            )
        return report_for(list(servers), str(remote or ""), progress, cancel_event)

    def search_exact_login(self, server, creds, login):
        if not configured():
            return originals["search_exact_login"](server, creds, login)
        report = report_for([str(server)], str(login or ""))
        wanted = str(login or "")
        report.matches = [
            record for record in report.matches
            if record.server.lower() == str(server or "").lower() and record.login == wanted
        ]
        report.total = 1
        report.checked = 1 if not report.errors else 0
        return report

    def suggest_free_login_all(self, servers, creds, base_login, cancel_event=None, deadline_seconds=None):
        if not configured():
            return originals["suggest_free_login_all"](
                servers, creds, base_login,
                cancel_event=cancel_event, deadline_seconds=deadline_seconds,
            )
        base = str(base_login or "").strip()
        if not base:
            return "", []
        report = report_for(list(servers), base, cancel_event=cancel_event)
        if report.errors:
            return "", report.errors
        existing = {record.login for record in report.matches}
        if base not in existing:
            return base, []
        index = 1
        while f"{base}_{index}" in existing:
            index += 1
        return f"{base}_{index}", []

    search_service.search_login_all = types.MethodType(search_login_all, search_service)
    search_service.search_port_all = types.MethodType(search_port_all, search_service)
    search_service.search_remote_all = types.MethodType(search_remote_all, search_service)
    search_service.search_exact_login = types.MethodType(search_exact_login, search_service)
    search_service.suggest_free_login_all = types.MethodType(suggest_free_login_all, search_service)

    event(
        "CLOUD",
        "Центральный поиск VPNSync подключён",
        "логин, Remote Address и NAT-порт читаются из PostgreSQL одним запросом",
    )
    _INSTALLED = True
