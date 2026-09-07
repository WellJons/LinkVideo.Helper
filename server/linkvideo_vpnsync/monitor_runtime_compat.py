from __future__ import annotations

import json
import threading
import time
from typing import Any


def install_monitor_runtime_compat() -> None:
    """Runtime fixes for the staged central RouterOS monitor.

    PPP local/remote address values are TEXT because RouterOS profiles may use
    pool names. NAT rows are projected and sorted identically on both sides of
    the comparison so a different RouterOS print order does not generate fake
    change history.

    RouterOS can contain duplicate /ppp/secret rows with the same login. The
    database intentionally models one logical VPN client per login, so the live
    monitor canonicalizes duplicate rows deterministically instead of letting
    them alternately overwrite one database row. For the LinkVideo L2TP fleet,
    an enabled ``l2tp`` secret is preferred over ``any`` and other services.
    Every raw duplicate row is embedded in the encrypted recovery snapshot via
    private metadata on the canonical row, so recovery information is retained.

    Shutdown also waits for an already-running startup/event snapshot before the
    FastAPI lifespan closes the PostgreSQL pool. This avoids the former
    ``pool is already closed`` race during service restart.
    """

    from . import monitor as monitor_module

    sync_cls = monitor_module.SnapshotSync
    manager_cls = monitor_module.RouterOSMonitorManager
    if getattr(sync_cls, "_runtime_compat", False):
        return

    original_client_cls = monitor_module.RouterOSClient
    original_ports = sync_cls._ports

    class CanonicalSecretRouterOSClient(original_client_cls):
        _duplicate_warning_state: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
        _duplicate_warning_lock = threading.Lock()

        @staticmethod
        def _secret_rank(row: dict[str, str]) -> tuple[int, int, str]:
            disabled = str(row.get("disabled") or "").strip().lower() in {"true", "yes", "1", "on", "да"}
            service = str(row.get("service") or "").strip().lower()
            if service == "l2tp":
                service_rank = 0
            elif service == "any":
                service_rank = 1
            else:
                service_rank = 2
            return (1 if disabled else 0, service_rank, str(row.get(".id") or ""))

        def print(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, str]]:
            rows = super().print(path, params)
            if str(path).rstrip("/") != "/ppp/secret":
                return rows

            groups: dict[str, list[dict[str, str]]] = {}
            order: list[str] = []
            blank_rows: list[dict[str, str]] = []
            for row in rows:
                login = str(row.get("name") or "").strip()
                if not login:
                    blank_rows.append(row)
                    continue
                if login not in groups:
                    groups[login] = []
                    order.append(login)
                groups[login].append(row)

            duplicate_state: list[tuple[str, tuple[str, ...]]] = []
            result: list[dict[str, str]] = []
            for login in order:
                variants = groups[login]
                if len(variants) == 1:
                    result.append(variants[0])
                    continue

                canonical = dict(sorted(variants, key=self._secret_rank)[0])
                ids = tuple(sorted(str(item.get(".id") or "") for item in variants))
                duplicate_state.append((login, ids))

                # These private keys are ignored by normal field extraction but
                # become part of the encrypted recovery_snapshot written by the
                # monitor. Do not expose the variant payload through API/search.
                canonical["_vpnsync_duplicate_count"] = str(len(variants))
                canonical["_vpnsync_duplicate_ids"] = ",".join(ids)
                canonical["_vpnsync_duplicate_variants_json"] = json.dumps(
                    variants,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                result.append(canonical)

            result.extend(blank_rows)
            state = tuple(sorted(duplicate_state))
            host_key = str(getattr(self, "host", "") or "").lower()
            with self._duplicate_warning_lock:
                previous = self._duplicate_warning_state.get(host_key)
                if state:
                    self._duplicate_warning_state[host_key] = state
                else:
                    self._duplicate_warning_state.pop(host_key, None)
            if state and state != previous:
                details = ", ".join(
                    f"{login} ({len(ids)} rows: {','.join(ids)})"
                    for login, ids in state
                )
                print(
                    f"[ROUTEROS] {self.host}: duplicate PPP secret login(s) detected; "
                    f"using one logical client per login: {details}",
                    flush=True,
                )
            elif not state and previous:
                print(f"[ROUTEROS] {self.host}: duplicate PPP secret condition cleared", flush=True)

            return result

    def canonical_ports(rules: list[dict[str, str]]) -> list[dict[str, Any]]:
        rows = list(original_ports(rules))
        normalized: list[dict[str, Any]] = []
        for row in rows:
            normalized.append({
                "external_port": int(row.get("external_port") or 0),
                "internal_port": int(row.get("internal_port") or row.get("external_port") or 0),
                "protocol": str(row.get("protocol") or "tcp").lower(),
                "to_address": str(row.get("to_address") or ""),
                "disabled": bool(row.get("disabled", False)),
                "routeros_rule_id": str(row.get("routeros_rule_id") or ""),
            })
        normalized.sort(key=lambda item: (
            int(item["external_port"]),
            str(item["protocol"]),
            int(item["internal_port"]),
            str(item["to_address"]),
            bool(item["disabled"]),
            str(item["routeros_rule_id"]),
        ))
        return normalized

    def existing(self, server_id: int) -> dict[str, dict[str, Any]]:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.login, c.lifecycle_state, c.disabled,
                       c.first_seen_at, c.last_seen_at,
                       c.remote_address AS remote_address,
                       c.local_address AS local_address,
                       c.profile, c.service, c.routeros_comment,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', lower(p.protocol),
                               'to_address', COALESCE(host(p.to_address), ''),
                               'disabled', p.disabled,
                               'routeros_rule_id', p.routeros_rule_id
                           ) ORDER BY p.external_port, lower(p.protocol), p.internal_port,
                                      COALESCE(host(p.to_address), ''), p.disabled, p.routeros_rule_id)
                           FROM vpn_nat_ports p
                           WHERE p.client_id = c.id AND p.deleted = FALSE
                       ), '[]'::jsonb) AS ports
                  FROM vpn_clients c
                 WHERE c.server_id = %s
                """,
                (int(server_id),),
            )
            return {str(row["login"]): dict(row) for row in cur.fetchall()}

    def start(self) -> None:
        self._stopped.clear()
        self._startup_threads = []
        self._inflight_threads = set()
        self._inflight_lock = threading.Lock()
        if not self.targets:
            print("[ROUTEROS] central monitor disabled/not configured", flush=True)
            return
        for target in self.targets:
            thread = threading.Thread(
                target=self._startup_sync,
                args=(target,),
                daemon=True,
                name=f"vpnsync-start:{target.host}",
            )
            self._startup_threads.append(thread)
            thread.start()
            listener = monitor_module.RouterOSListener(target, self._on_event)
            self.listeners.append(listener)
            listener.start()
        print(f"[ROUTEROS] event monitor started for {len(self.targets)} servers", flush=True)

    def event_sync(self, target, path: str, payload: dict[str, str]) -> None:
        current = threading.current_thread()
        lock = getattr(self, "_inflight_lock", None)
        inflight = getattr(self, "_inflight_threads", None)
        if lock is not None and inflight is not None:
            with lock:
                inflight.add(current)
        try:
            with self._timer_lock:
                self._timers.pop(target.host, None)
            if self._stopped.is_set():
                return
            try:
                self.syncer.sync(target, event_path=path, event_payload=payload)
            except Exception as exc:
                if not self._stopped.is_set():
                    print(f"[SYNC] {target.host}: event sync failed: {exc}", flush=True)
        finally:
            if lock is not None and inflight is not None:
                with lock:
                    inflight.discard(current)

    def stop(self) -> None:
        self._stopped.set()
        for listener in self.listeners:
            listener.stop()

        with self._timer_lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

        deadline = time.monotonic() + 35.0
        current = threading.current_thread()

        # A Timer removes itself from _timers before the expensive snapshot, so
        # running event snapshots are tracked separately in _inflight_threads.
        lock = getattr(self, "_inflight_lock", None)
        if lock is not None:
            with lock:
                inflight = list(getattr(self, "_inflight_threads", set()))
        else:
            inflight = []
        workers = list(getattr(self, "_startup_threads", [])) + inflight
        for worker in workers:
            if worker is current or not worker.is_alive():
                continue
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            worker.join(timeout=remaining)

        # Listener sockets were closed above; give their read loops a moment to
        # leave cleanly as well.
        for listener in self.listeners:
            thread = getattr(listener, "_thread", None)
            if thread is None or thread is current or not thread.is_alive():
                continue
            remaining = min(3.0, max(0.0, deadline - time.monotonic()))
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

    def status(self) -> dict[str, Any]:
        target_hosts = [target.host for target in self.targets]
        listener_by_host = {listener.target.host: listener for listener in self.listeners}
        db_rows: dict[str, dict[str, Any]] = {}
        if target_hosts:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT hostname, active_l2tp, last_sync_at, last_event_at
                      FROM vpn_servers
                     WHERE hostname = ANY(%s)
                    """,
                    (target_hosts,),
                )
                db_rows = {str(row["hostname"]): dict(row) for row in cur.fetchall()}

        servers: list[dict[str, Any]] = []
        workers_alive = 0
        connected_count = 0
        for target in self.targets:
            listener = listener_by_host.get(target.host)
            thread = getattr(listener, "_thread", None) if listener is not None else None
            worker_alive = bool(thread and thread.is_alive())
            if worker_alive:
                workers_alive += 1

            connected = False
            connected_port = None
            if listener is not None:
                client_lock = getattr(listener, "_client_lock", None)
                if client_lock is not None:
                    with client_lock:
                        client = getattr(listener, "_client", None)
                        connected = bool(client is not None and getattr(client, "sock", None) is not None)
                        connected_port = getattr(client, "connected_port", None) if connected else None
                else:
                    client = getattr(listener, "_client", None)
                    connected = bool(client is not None and getattr(client, "sock", None) is not None)
                    connected_port = getattr(client, "connected_port", None) if connected else None
            if connected:
                connected_count += 1

            db_row = db_rows.get(target.host, {})
            servers.append({
                "host": target.host,
                "worker_alive": worker_alive,
                "connected": connected,
                "connected_port": connected_port,
                "active_l2tp": int(db_row.get("active_l2tp") or 0),
                "last_sync_at": db_row.get("last_sync_at"),
                "last_event_at": db_row.get("last_event_at"),
            })

        return {
            "enabled": bool(self.targets),
            "target_count": len(self.targets),
            "workers_alive": workers_alive,
            "connected_count": connected_count,
            "servers": servers,
        }

    monitor_module.RouterOSClient = CanonicalSecretRouterOSClient
    sync_cls._ports = staticmethod(canonical_ports)
    sync_cls._existing = existing
    manager_cls.start = start
    manager_cls.stop = stop
    manager_cls._event_sync = event_sync
    manager_cls.status = status
    sync_cls._runtime_compat = True
