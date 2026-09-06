from __future__ import annotations

import threading
from typing import Callable

from linkvideo_vpn_helper.services.app_logging import event, error
from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient
from linkvideo_vpn_helper.services.vpn_service import VPNService


_INSTALLED = False


def install_cloud_activity_bridge(service: VPNService, settings) -> None:
    """Mirror direct Helper RouterOS actions into central audit.

    Interactive work remains direct to MikroTik. Audit transport is asynchronous
    and best-effort: VPNSync/PostgreSQL availability must never change the result
    of the RouterOS operation itself.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    client = CloudVPNSyncClient(settings)
    lock = threading.Lock()
    service._cloud_vpnsync_client = client
    service._cloud_activity_lock = lock

    methods = {
        "create_clients_batch": "client.create",
        "add_ports": "nat.add_ports",
        "remove_port": "nat.remove_port",
        "set_password": "client.password_change",
        "set_secret_enabled": "client.enabled_change",
        "set_port_enabled": "nat.enabled_change",
        "recreate_port": "nat.recreate",
        "delete_client": "client.delete",
        "disconnect_client_session": "client.disconnect",
    }

    def submit(action: str, *, server: str, login: str, success: bool, details: dict | None = None) -> None:
        def worker():
            try:
                fresh = client.store.load()
                if not fresh.username or not fresh.password:
                    return
                with lock:
                    current = client.config
                    if (
                        fresh.host != current.host
                        or int(fresh.port) != int(current.port)
                        or fresh.username != current.username
                        or fresh.password != current.password
                        or bool(fresh.use_tls) != bool(current.use_tls)
                    ):
                        client.set_config(fresh, save=False)
                    client.record_activity(
                        action,
                        server=server,
                        login=login,
                        success=success,
                        details=details or {},
                    )
            except Exception as exc:
                error("CLOUD", "Не удалось отправить действие в VPNSync", exc)

        threading.Thread(target=worker, daemon=True, name="vpnsync-desktop-audit").start()

    for method_name, action in methods.items():
        original = getattr(VPNService, method_name, None)
        if not callable(original):
            continue

        def make_wrapper(fn: Callable, action_name: str, wrapped_name: str):
            def wrapper(self, *args, **kwargs):
                server = str(args[0] if args else kwargs.get("server", "") or "")
                login = str(args[2] if len(args) >= 3 else kwargs.get("login", kwargs.get("base_login", "")) or "")
                try:
                    result = fn(self, *args, **kwargs)
                except Exception as exc:
                    submit(
                        action_name,
                        server=server,
                        login=login,
                        success=False,
                        details={"error": str(exc)[:600]},
                    )
                    raise

                if wrapped_name == "create_clients_batch":
                    records = list(result or [])
                    if records:
                        for record in records:
                            record_login = str(getattr(record, "login", "") or login)
                            submit(action_name, server=server, login=record_login, success=True)
                    else:
                        submit(action_name, server=server, login=login, success=True)
                else:
                    submit(action_name, server=server, login=login, success=True)
                return result

            wrapper.__name__ = getattr(fn, "__name__", wrapped_name)
            wrapper.__doc__ = getattr(fn, "__doc__", None)
            return wrapper

        setattr(VPNService, method_name, make_wrapper(original, action, method_name))

    event(
        "CLOUD",
        "Аудит действий Helper подключён",
        "операции остаются прямыми к MikroTik; аудит отправляется асинхронно",
    )
    _INSTALLED = True
