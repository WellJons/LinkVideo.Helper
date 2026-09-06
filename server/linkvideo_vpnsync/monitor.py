from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .db import VPNDatabase
from .routeros import RouterOSClient, RouterOSListener, RouterTarget


_LV_MARKER_RE = re.compile(r"\|LV(?:1|2)\|(?:[^|]*\|)+", re.I)


def _country(host: str) -> str:
    value = str(host or "").strip().lower()
    if value.startswith("kz-"):
        return "Казахстан"
    if value.startswith("rb-") or value.startswith("by-"):
        return "Беларусь"
    return "Россия"


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "1", "on", "да"}


def _clean_comment(value: Any) -> str:
    return _LV_MARKER_RE.sub("", str(value or "")).strip(" |")


def _parse_routeros_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or re.match(r"^jan/01/1970\b", text, re.I):
        return None
    for pattern in ("%b/%d/%Y %H:%M:%S", "%b/%d/%Y %H:%M"):
        try:
            result = datetime.strptime(text, pattern)
            return result.replace(tzinfo=datetime.now().astimezone().tzinfo)
        except ValueError:
            pass
    try:
        result = datetime.fromisoformat(text)
        return result.astimezone() if result.tzinfo else result.replace(tzinfo=datetime.now().astimezone().tzinfo)
    except ValueError:
        return None


def _next_action(last_seen: datetime | None, first_seen: datetime | None) -> tuple[datetime | None, str | None]:
    now = datetime.now().astimezone()
    if last_seen is None:
        if first_seen is None:
            return None, None
        due = first_seen + timedelta(days=30)
        return (due if due > now else now), "delete_never_active"
    sleep_at = last_seen + timedelta(days=30)
    quarantine_at = last_seen + timedelta(days=90)
    delete_at = last_seen + timedelta(days=365)
    if now < sleep_at:
        return sleep_at, "sleep"
    if now < quarantine_at:
        return quarantine_at, "quarantine"
    if now < delete_at:
        return delete_at, "delete"
    return now, "delete"


def _state(existing: str, *, disabled: bool, is_active: bool, last_seen: datetime | None) -> str:
    existing = str(existing or "unknown")
    if is_active:
        return "active"
    if disabled:
        if existing in {"quarantine", "manual_disabled"}:
            return existing
        return "manual_disabled"
    if last_seen is None:
        return "never_active"
    age = datetime.now().astimezone() - last_seen
    if age >= timedelta(days=90):
        return "quarantine_due"
    if age >= timedelta(days=30):
        return "sleeping"
    return "active"


def _parse_port(value: Any) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() and 1 <= int(text) <= 65535 else None


def load_targets(settings: Settings) -> list[RouterTarget]:
    if not settings.routeros_monitor_enabled:
        return []
    common_user = str(settings.routeros_username or "")
    common_password = str(settings.routeros_password or "")
    source: list[dict[str, Any]] = []
    path = Path(settings.routeros_servers_file)
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("servers", [])
        if isinstance(payload, list):
            source = [item for item in payload if isinstance(item, dict)]
    else:
        source = [
            {"host": host.strip(), "country": _country(host), "enabled": True}
            for host in str(settings.routeros_servers or "").split(",")
            if host.strip()
        ]

    result: list[RouterTarget] = []
    for item in source:
        if item.get("enabled", True) is False:
            continue
        host = str(item.get("host") or "").strip().lower()
        user = str(item.get("username") or common_user)
        password = str(item.get("password") or common_password)
        if not host or not user or not password:
            continue
        result.append(RouterTarget(
            host=host,
            country=str(item.get("country") or _country(host)),
            username=user,
            password=password,
            port=int(item.get("port") or settings.routeros_api_port),
            timeout=float(item.get("timeout") or settings.routeros_timeout),
        ))
    return result


class SnapshotSync:
    def __init__(self, db: VPNDatabase) -> None:
        self.db = db
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, host: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(host, threading.Lock())

    def _existing(self, server_id: int) -> dict[str, dict[str, Any]]:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.login, c.lifecycle_state, c.disabled,
                       c.first_seen_at, c.last_seen_at,
                       host(c.remote_address) AS remote_address,
                       host(c.local_address) AS local_address,
                       c.profile, c.service, c.routeros_comment,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', p.protocol,
                               'disabled', p.disabled
                           ) ORDER BY p.external_port)
                           FROM vpn_nat_ports p
                           WHERE p.client_id = c.id AND p.deleted = FALSE
                       ), '[]'::jsonb) AS ports
                  FROM vpn_clients c
                 WHERE c.server_id = %s
                """,
                (int(server_id),),
            )
            return {str(row["login"]): dict(row) for row in cur.fetchall()}

    @staticmethod
    def _nat_by_client(
        secrets: list[dict[str, str]],
        profiles: dict[str, dict[str, str]],
        active: dict[str, dict[str, str]],
        nat_rules: list[dict[str, str]],
    ) -> dict[str, list[dict[str, str]]]:
        remote_to_logins: dict[str, list[str]] = {}
        login_remote: dict[str, str] = {}
        login_set = {str(secret.get("name") or "") for secret in secrets}
        for secret in secrets:
            login = str(secret.get("name") or "")
            profile_name = str(secret.get("profile") or "")
            profile = profiles.get(profile_name, {})
            remote = str(profile.get("remote-address") or active.get(login, {}).get("address") or "").strip()
            login_remote[login] = remote
            if remote:
                remote_to_logins.setdefault(remote, []).append(login)

        result: dict[str, list[dict[str, str]]] = {login: [] for login in login_set if login}
        for rule in nat_rules:
            if str(rule.get("chain") or "") != "dstnat" or str(rule.get("action") or "") != "dst-nat":
                continue
            comment = str(rule.get("comment") or "").strip()
            to_address = str(rule.get("to-addresses") or "").strip()
            login = ""
            if comment in login_set:
                remote = login_remote.get(comment, "")
                if not to_address or not remote or to_address == remote:
                    login = comment
            if not login and to_address:
                candidates = remote_to_logins.get(to_address, [])
                if len(candidates) == 1:
                    login = candidates[0]
            if login:
                result.setdefault(login, []).append(rule)
        return result

    @staticmethod
    def _ports(rules: list[dict[str, str]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for rule in rules:
            external = _parse_port(rule.get("dst-port"))
            if external is None:
                continue
            internal = _parse_port(rule.get("to-ports")) or external
            protocol = str(rule.get("protocol") or "tcp").lower()
            key = (protocol, external)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "external_port": external,
                "internal_port": internal,
                "protocol": protocol,
                "to_address": str(rule.get("to-addresses") or ""),
                "disabled": _bool(rule.get("disabled")),
                "routeros_rule_id": str(rule.get(".id") or ""),
            })
        return result

    def sync(self, target: RouterTarget, *, event_path: str = "startup", event_payload: dict[str, str] | None = None) -> dict[str, int]:
        with self._lock(target.host):
            server_id = self.db.ensure_server(target.host, target.country)
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                secrets = api.print("/ppp/secret")
                active_rows = api.print("/ppp/active")
                profile_rows = api.print("/ppp/profile")
                nat_rows = api.print("/ip/firewall/nat")

            active = {str(row.get("name") or ""): row for row in active_rows if str(row.get("name") or "")}
            profiles = {str(row.get("name") or ""): row for row in profile_rows if str(row.get("name") or "")}
            nat_for = self._nat_by_client(secrets, profiles, active, nat_rows)
            before = self._existing(server_id)
            now = datetime.now().astimezone()
            current_logins: set[str] = set()
            added = changed = deleted = 0

            for secret in secrets:
                login = str(secret.get("name") or "").strip()
                if not login:
                    continue
                current_logins.add(login)
                old = before.get(login, {})
                profile_name = str(secret.get("profile") or "")
                profile = profiles.get(profile_name, {})
                active_row = active.get(login, {})
                is_active = bool(active_row)
                remote = str(profile.get("remote-address") or active_row.get("address") or old.get("remote_address") or "")
                local = str(profile.get("local-address") or old.get("local_address") or "")
                first_seen = old.get("first_seen_at") or now
                last_seen = now if is_active else (_parse_routeros_dt(secret.get("last-logged-out")) or old.get("last_seen_at"))
                disabled = _bool(secret.get("disabled"))
                lifecycle = _state(str(old.get("lifecycle_state") or "unknown"), disabled=disabled, is_active=is_active, last_seen=last_seen)
                next_at, next_type = _next_action(last_seen, first_seen)
                rules = nat_for.get(login, [])
                port_rows = self._ports(rules)
                snapshot = {
                    "secret": secret,
                    "active": active_row,
                    "profile": profile,
                    "nat_rules": rules,
                }
                base_comment = _clean_comment(secret.get("comment"))

                client_id = self.db.upsert_client(
                    server_id=server_id,
                    login=login,
                    remote_address=remote,
                    local_address=local,
                    profile=profile_name,
                    service=str(secret.get("service") or ""),
                    disabled=disabled,
                    lifecycle_state=lifecycle,
                    last_seen_at=last_seen,
                    first_seen_at=first_seen,
                    next_action_at=next_at,
                    next_action_type=next_type,
                    password=str(secret.get("password") or ""),
                    recovery_snapshot=snapshot,
                    routeros_comment=base_comment,
                )
                self.db.replace_nat_ports(server_id, client_id, port_rows)
                self.db.remove_deleted_client(server_id, login)

                visible_old = {
                    "remote": str(old.get("remote_address") or ""),
                    "local": str(old.get("local_address") or ""),
                    "profile": str(old.get("profile") or ""),
                    "service": str(old.get("service") or ""),
                    "disabled": bool(old.get("disabled", False)),
                    "state": str(old.get("lifecycle_state") or ""),
                    "comment": str(old.get("routeros_comment") or ""),
                    "ports": old.get("ports") or [],
                }
                visible_new = {
                    "remote": remote,
                    "local": local,
                    "profile": profile_name,
                    "service": str(secret.get("service") or ""),
                    "disabled": disabled,
                    "state": lifecycle,
                    "comment": base_comment,
                    "ports": port_rows,
                }
                if not old:
                    added += 1
                    self.db.append_change(
                        server_id=server_id,
                        client_id=client_id,
                        login=login,
                        event_type="created",
                        summary="Новая VPN-учётка",
                        new_value=visible_new,
                        source="RouterOS event" if event_path != "startup" else "VPNSync startup",
                    )
                elif visible_old != visible_new:
                    changed += 1
                    self.db.append_change(
                        server_id=server_id,
                        client_id=client_id,
                        login=login,
                        event_type="changed",
                        summary="Конфигурация VPN изменена",
                        old_value=visible_old,
                        new_value=visible_new,
                        source="RouterOS event" if event_path != "startup" else "VPNSync startup",
                    )

            missing = sorted(set(before) - current_logins)
            for login in missing:
                if self.db.archive_client(
                    server_id,
                    login,
                    deleted_reason="Удалена непосредственно в RouterOS",
                    deleted_by="RouterOS",
                    source="RouterOS event",
                ):
                    deleted += 1
                    self.db.append_change(
                        server_id=server_id,
                        client_id=None,
                        login=login,
                        event_type="deleted",
                        summary="VPN-учётка удалена в RouterOS",
                        source="RouterOS event",
                    )

            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE vpn_servers
                       SET active_l2tp = %s,
                           last_sync_at = now(),
                           last_event_at = CASE WHEN %s = 'startup' THEN last_event_at ELSE now() END,
                           updated_at = now()
                     WHERE id = %s
                    """,
                    (len(active_rows), event_path, server_id),
                )
                if event_path != "startup":
                    cur.execute(
                        """
                        INSERT INTO sync_events(server_id, routeros_path, routeros_item_id, event_kind, observed_at, processed_at, payload)
                        VALUES (%s, %s, %s, 'changed', now(), now(), %s::jsonb)
                        """,
                        (
                            server_id,
                            event_path,
                            str((event_payload or {}).get(".id") or ""),
                            json.dumps(event_payload or {}, ensure_ascii=False),
                        ),
                    )
                conn.commit()

            print(
                f"[SYNC] {target.host}: clients={len(current_logins)} active={len(active_rows)} "
                f"added={added} changed={changed} deleted={deleted}",
                flush=True,
            )
            return {"clients": len(current_logins), "active": len(active_rows), "added": added, "changed": changed, "deleted": deleted}


class RouterOSMonitorManager:
    """One startup snapshot + RouterOS listen streams; no interval polling."""

    def __init__(self, db: VPNDatabase, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.targets = load_targets(settings)
        self.syncer = SnapshotSync(db)
        self.listeners: list[RouterOSListener] = []
        self._timers: dict[str, threading.Timer] = {}
        self._timer_lock = threading.Lock()
        self._stopped = threading.Event()

    @property
    def enabled(self) -> bool:
        return bool(self.targets)

    def start(self) -> None:
        if not self.targets:
            print("[ROUTEROS] central monitor disabled/not configured", flush=True)
            return
        for target in self.targets:
            threading.Thread(target=self._startup_sync, args=(target,), daemon=True, name=f"vpnsync-start:{target.host}").start()
            listener = RouterOSListener(target, self._on_event)
            self.listeners.append(listener)
            listener.start()
        print(f"[ROUTEROS] event monitor started for {len(self.targets)} servers", flush=True)

    def stop(self) -> None:
        self._stopped.set()
        for listener in self.listeners:
            listener.stop()
        with self._timer_lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _startup_sync(self, target: RouterTarget) -> None:
        try:
            self.syncer.sync(target, event_path="startup")
        except Exception as exc:
            print(f"[SYNC] {target.host}: startup sync failed: {exc}", flush=True)

    def _on_event(self, target: RouterTarget, path: str, payload: dict[str, str]) -> None:
        if self._stopped.is_set():
            return
        # A single user operation often changes secret/profile/NAT in a burst.
        # Debounce for one second, then capture one coherent full snapshot.
        with self._timer_lock:
            old = self._timers.pop(target.host, None)
            if old:
                old.cancel()
            timer = threading.Timer(1.0, self._event_sync, args=(target, path, dict(payload)))
            timer.daemon = True
            self._timers[target.host] = timer
            timer.start()

    def _event_sync(self, target: RouterTarget, path: str, payload: dict[str, str]) -> None:
        with self._timer_lock:
            self._timers.pop(target.host, None)
        if self._stopped.is_set():
            return
        try:
            self.syncer.sync(target, event_path=path, event_payload=payload)
        except Exception as exc:
            print(f"[SYNC] {target.host}: event sync failed: {exc}", flush=True)
