from __future__ import annotations

import threading
from typing import Any, Callable

from linkvideo_vpn_helper.services.app_logging import event
from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, VPNService


_INSTALLED = False


def _as_int_list(value) -> list[int]:
    result: list[int] = []
    for item in list(value or []):
        try:
            port = int(item)
        except Exception:
            continue
        if 1 <= port <= 65535 and port not in result:
            result.append(port)
    return sorted(result)


def _record(payload: dict[str, Any] | None) -> ClientRecord:
    row = dict(payload or {})
    ports = _as_int_list(row.get("ports"))
    disabled_ports = _as_int_list(row.get("disabled_ports"))
    # Server-side DB state knows whether the NAT rule is enabled, but not whether
    # a live conntrack entry currently exists. Do not mislabel enabled NAT as
    # active traffic; the normal read/diagnostics path can refresh that separately.
    return ClientRecord(
        server=str(row.get("server") or ""),
        login=str(row.get("login") or ""),
        password=str(row.get("password") or ""),
        remote_address=str(row.get("remote_address") or ""),
        ports=ports,
        profile_id=str(row.get("profile_id") or ""),
        secret_id=str(row.get("secret_id") or ""),
        nat_rule_ids=[str(x) for x in list(row.get("nat_rule_ids") or []) if str(x or "")],
        is_online=bool(row.get("is_online", False)),
        active_ports=[],
        disabled_ports=disabled_ports,
        is_enabled=bool(row.get("is_enabled", True)),
        last_logged_out=str(row.get("last_logged_out") or ""),
        uptime=str(row.get("uptime") or ""),
    )


def install_cloud_operations_bridge(service: VPNService, settings) -> None:
    """Make central VPNSync the only writer once cloud credentials are configured.

    Reads may still use the mature direct RouterOS path during the migration, but
    all employee mutations go through authenticated VPNSync endpoints. If the
    cloud server is configured and unavailable, the mutation fails explicitly;
    there is intentionally no silent direct-RouterOS fallback that could bypass
    PostgreSQL/audit consistency.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    client = getattr(service, "_cloud_vpnsync_client", None)
    if not isinstance(client, CloudVPNSyncClient):
        client = CloudVPNSyncClient(settings)
        service._cloud_vpnsync_client = client
    lock = getattr(service, "_cloud_activity_lock", None)
    if lock is None:
        lock = threading.RLock()
        service._cloud_activity_lock = lock

    originals: dict[str, Callable] = {}
    for name in (
        "create_clients_batch",
        "add_ports",
        "remove_port",
        "set_password",
        "set_secret_enabled",
        "set_port_enabled",
        "recreate_port",
        "delete_client",
        "disconnect_client_session",
    ):
        fn = getattr(VPNService, name, None)
        if callable(fn):
            originals[name] = fn

    def configured(self) -> tuple[bool, CloudVPNSyncClient]:
        cloud = getattr(self, "_cloud_vpnsync_client", client)
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
        return ready, cloud

    def cloud_request(self, path: str, payload: dict[str, Any]):
        ready, cloud = configured(self)
        if not ready:
            return False, None
        with lock:
            return True, cloud.request("POST", path, json=payload)

    def create_clients_batch(self, server, creds, base_login, ports_per_client, accounts_count, progress_callback=None, cancel_event=None):
        ready, cloud = configured(self)
        if not ready:
            return originals["create_clients_batch"](
                self, server, creds, base_login, ports_per_client, accounts_count,
                progress_callback=progress_callback, cancel_event=cancel_event,
            )
        if cancel_event is not None and cancel_event.is_set():
            from linkvideo_vpn_helper.services.errors import OperationCancelled
            raise OperationCancelled("Операция отменена пользователем")
        if progress_callback:
            try:
                progress_callback(0, max(1, int(accounts_count)), str(base_login or ""))
            except Exception:
                pass
        with lock:
            rows = cloud.request(
                "POST",
                "/v1/operations/clients/create",
                json={
                    "server": str(server),
                    "base_login": str(base_login),
                    "ports_per_client": int(ports_per_client),
                    "accounts_count": int(accounts_count),
                },
            )
        records = [_record(row) for row in list(rows or [])]
        if progress_callback:
            try:
                progress_callback(len(records), max(1, int(accounts_count)), records[-1].login if records else str(base_login or ""))
            except Exception:
                pass
        return records

    def add_ports(self, server, creds, login, count):
        used, row = cloud_request(self, "/v1/operations/ports/add", {
            "server": str(server), "login": str(login), "count": int(count),
        })
        return _record(row) if used else originals["add_ports"](self, server, creds, login, count)

    def remove_port(self, server, creds, login, port):
        used, row = cloud_request(self, "/v1/operations/ports/remove", {
            "server": str(server), "login": str(login), "port": int(port),
        })
        return _record(row) if used else originals["remove_port"](self, server, creds, login, port)

    def set_password(self, server, creds, login, new_password):
        used, row = cloud_request(self, "/v1/operations/clients/password", {
            "server": str(server), "login": str(login), "password": str(new_password),
        })
        return _record(row) if used else originals["set_password"](self, server, creds, login, new_password)

    def set_secret_enabled(self, server, creds, login, enabled):
        used, row = cloud_request(self, "/v1/operations/clients/enabled", {
            "server": str(server), "login": str(login), "enabled": bool(enabled),
        })
        return _record(row) if used else originals["set_secret_enabled"](self, server, creds, login, enabled)

    def set_port_enabled(self, server, creds, login, port, enabled):
        used, row = cloud_request(self, "/v1/operations/ports/enabled", {
            "server": str(server), "login": str(login), "port": int(port), "enabled": bool(enabled),
        })
        return _record(row) if used else originals["set_port_enabled"](self, server, creds, login, port, enabled)

    def recreate_port(self, server, creds, login, port):
        used, row = cloud_request(self, "/v1/operations/ports/recreate", {
            "server": str(server), "login": str(login), "port": int(port),
        })
        return _record(row) if used else originals["recreate_port"](self, server, creds, login, port)

    def delete_client(self, server, creds, login):
        used, _row = cloud_request(self, "/v1/operations/clients/delete", {
            "server": str(server), "login": str(login),
        })
        if used:
            return None
        return originals["delete_client"](self, server, creds, login)

    def disconnect_client_session(self, server, creds, login):
        used, row = cloud_request(self, "/v1/operations/clients/disconnect", {
            "server": str(server), "login": str(login),
        })
        if used:
            return bool((row or {}).get("disconnected"))
        return originals["disconnect_client_session"](self, server, creds, login)

    VPNService.create_clients_batch = create_clients_batch
    VPNService.add_ports = add_ports
    VPNService.remove_port = remove_port
    VPNService.set_password = set_password
    VPNService.set_secret_enabled = set_secret_enabled
    VPNService.set_port_enabled = set_port_enabled
    VPNService.recreate_port = recreate_port
    VPNService.delete_client = delete_client
    VPNService.disconnect_client_session = disconnect_client_session

    event(
        "CLOUD",
        "Центральные операции VPNSync подключены",
        "при настроенном облачном сервере все изменения RouterOS выполняются сервером",
    )
    _INSTALLED = True
