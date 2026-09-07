from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .monitor import load_targets
from .routeros import RouterOSClient, RouterTarget


class ArchiveRestoreConflict(RuntimeError):
    def __init__(self, message: str, conflicts: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.conflicts = list(conflicts or [])


@dataclass(slots=True)
class RestorePlan:
    server: str
    login: str
    source: str
    remote_address: str
    local_address: str
    profile: str
    service: str
    disabled: bool
    password: str
    comment: str
    secret_snapshot: dict[str, Any]
    profile_snapshot: dict[str, Any]
    ports: list[dict[str, Any]]


class ArchiveRestoreService:
    """Fail-closed recovery from PostgreSQL backup/archive to live RouterOS.

    PostgreSQL is never used for normal operator search or mutation. Recovery is
    exceptional and always checks the live MikroTik immediately before any add.
    A conflict prevents the whole restore attempt; nothing is merged blindly.
    """

    PROFILE_WRITABLE = (
        "name", "local-address", "remote-address", "bridge", "use-compression",
        "use-encryption", "use-ipv6", "use-mpls", "use-upnp", "only-one",
        "change-tcp-mss", "dns-server", "wins-server", "rate-limit",
        "session-timeout", "idle-timeout", "address-list", "interface-list",
        "parent-queue", "queue-type", "on-up", "on-down",
    )
    SECRET_WRITABLE = (
        "name", "password", "service", "profile", "local-address",
        "remote-address", "routes", "caller-id", "limit-bytes-in",
        "limit-bytes-out", "comment", "disabled",
    )

    def __init__(self, db, settings, monitor) -> None:
        self.db = db
        self.settings = settings
        self.monitor = monitor
        self.targets = {target.host.lower(): target for target in load_targets(settings)}

    @staticmethod
    def _bool(value: Any) -> bool:
        return str(value or "").strip().lower() in {"yes", "true", "1", "on", "да"}

    @staticmethod
    def _clean(source: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in allowed:
            value = source.get(key)
            if value not in (None, ""):
                result[key] = str(value)
        return result

    @staticmethod
    def _json(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _parse_ports(value: Any) -> set[int]:
        text = str(value or "").strip()
        if not text:
            return set()
        result: set[int] = set()
        for token in re.split(r"[,;\s]+", text):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                left, right = token.split("-", 1)
                if left.isdigit() and right.isdigit():
                    start, end = int(left), int(right)
                    if 1 <= start <= end <= 65535 and end - start <= 4096:
                        result.update(range(start, end + 1))
                continue
            if token.isdigit():
                port = int(token)
                if 1 <= port <= 65535:
                    result.add(port)
        return result

    def _target(self, server: str) -> RouterTarget:
        host = str(server or "").strip().lower()
        target = self.targets.get(host)
        if target is None:
            raise ValueError(f"RouterOS server is not configured: {server}")
        return target

    def _load_plan(self, server: str, login: str) -> RestorePlan:
        host = str(server or "").strip().lower()
        wanted = str(login or "").strip()
        if not host or not wanted:
            raise ValueError("server/login is empty")

        with self.db.connection() as conn, conn.cursor() as cur:
            # Deleted archive is preferred. If it is absent, current passive
            # backup may still be used for disaster recovery of a wiped router.
            cur.execute(
                """
                SELECT 'deleted' AS backup_source, d.login,
                       host(d.remote_address) AS remote_address,
                       host(d.local_address) AS local_address,
                       d.profile, d.service, d.routeros_comment, d.ports,
                       CASE WHEN d.password_enc IS NULL THEN ''
                            ELSE pgp_sym_decrypt(d.password_enc, %s) END AS password,
                       CASE WHEN d.recovery_snapshot_enc IS NULL THEN ''
                            ELSE pgp_sym_decrypt(d.recovery_snapshot_enc, %s) END AS snapshot
                  FROM vpn_deleted_clients d
                  JOIN vpn_servers s ON s.id=d.server_id
                 WHERE lower(s.hostname)=lower(%s) AND d.login=%s
                UNION ALL
                SELECT 'active-backup' AS backup_source, c.login,
                       host(c.remote_address) AS remote_address,
                       host(c.local_address) AS local_address,
                       c.profile, c.service, c.routeros_comment,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', p.protocol,
                               'to_address', host(p.to_address),
                               'disabled', p.disabled
                           ) ORDER BY p.external_port)
                             FROM vpn_nat_ports p
                            WHERE p.client_id=c.id AND p.deleted=FALSE
                       ), '[]'::jsonb) AS ports,
                       CASE WHEN c.password_enc IS NULL THEN ''
                            ELSE pgp_sym_decrypt(c.password_enc, %s) END AS password,
                       CASE WHEN c.recovery_snapshot_enc IS NULL THEN ''
                            ELSE pgp_sym_decrypt(c.recovery_snapshot_enc, %s) END AS snapshot
                  FROM vpn_clients c
                  JOIN vpn_servers s ON s.id=c.server_id
                 WHERE lower(s.hostname)=lower(%s) AND c.login=%s
                 LIMIT 1
                """,
                (
                    self.db.encryption_key, self.db.encryption_key, host, wanted,
                    self.db.encryption_key, self.db.encryption_key, host, wanted,
                ),
            )
            row = cur.fetchone()
        if not row:
            raise ValueError("VPN account backup was not found in PostgreSQL")

        snapshot = self._json(row.get("snapshot"))
        secret = snapshot.get("secret") if isinstance(snapshot.get("secret"), dict) else {}
        profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        ports = list(row.get("ports") or [])
        if not ports and isinstance(snapshot.get("nat_rules"), list):
            for rule in snapshot.get("nat_rules") or []:
                if not isinstance(rule, dict):
                    continue
                ext = next(iter(self._parse_ports(rule.get("dst-port"))), 0)
                if not ext:
                    continue
                internal = next(iter(self._parse_ports(rule.get("to-ports"))), ext)
                ports.append({
                    "external_port": ext,
                    "internal_port": internal,
                    "protocol": str(rule.get("protocol") or "tcp").lower(),
                    "to_address": str(rule.get("to-addresses") or row.get("remote_address") or ""),
                    "disabled": self._bool(rule.get("disabled")),
                })

        password = str(row.get("password") or secret.get("password") or "")
        if not password:
            raise ValueError("Backup has no recoverable VPN password; automatic restore is blocked")

        return RestorePlan(
            server=host,
            login=wanted,
            source=str(row.get("backup_source") or ""),
            remote_address=str(row.get("remote_address") or profile.get("remote-address") or secret.get("remote-address") or ""),
            local_address=str(row.get("local_address") or profile.get("local-address") or secret.get("local-address") or ""),
            profile=str(row.get("profile") or secret.get("profile") or wanted),
            service=str(row.get("service") or secret.get("service") or "l2tp"),
            disabled=self._bool(secret.get("disabled")),
            password=password,
            comment=str(row.get("routeros_comment") or secret.get("comment") or ""),
            secret_snapshot=dict(secret),
            profile_snapshot=dict(profile),
            ports=[dict(item) for item in ports if isinstance(item, dict)],
        )

    def _preflight_live(self, api: RouterOSClient, plan: RestorePlan) -> dict[str, Any]:
        secrets = api.print("/ppp/secret")
        profiles = api.print("/ppp/profile")
        nat_rules = api.print("/ip/firewall/nat")
        conflicts: list[dict[str, Any]] = []

        for item in secrets:
            if str(item.get("name") or "").strip() == plan.login:
                conflicts.append({"type": "login", "value": plan.login, "routeros_id": str(item.get(".id") or "")})

        if plan.profile and plan.profile not in {"default", "default-encryption"}:
            for item in profiles:
                if str(item.get("name") or "").strip() == plan.profile:
                    conflicts.append({"type": "profile", "value": plan.profile, "routeros_id": str(item.get(".id") or "")})

        if plan.remote_address:
            for item in secrets:
                remote = str(item.get("remote-address") or "").strip()
                if remote and remote == plan.remote_address:
                    conflicts.append({"type": "remote-address-secret", "value": plan.remote_address, "owner": str(item.get("name") or "")})
            for item in profiles:
                remote = str(item.get("remote-address") or "").strip()
                if remote and remote == plan.remote_address:
                    conflicts.append({"type": "remote-address-profile", "value": plan.remote_address, "owner": str(item.get("name") or "")})

        wanted_ports = {
            int(item.get("external_port") or 0)
            for item in plan.ports
            if str(item.get("external_port") or "").isdigit() and 1 <= int(item.get("external_port") or 0) <= 65535
        }
        for rule in nat_rules:
            overlap = wanted_ports & self._parse_ports(rule.get("dst-port"))
            for port in sorted(overlap):
                conflicts.append({
                    "type": "nat-port",
                    "value": port,
                    "routeros_id": str(rule.get(".id") or ""),
                    "comment": str(rule.get("comment") or ""),
                })

        return {
            "ok": not conflicts,
            "server": plan.server,
            "login": plan.login,
            "backup_source": plan.source,
            "profile": plan.profile,
            "remote_address": plan.remote_address,
            "ports": sorted(wanted_ports),
            "live_secret_count": len(secrets),
            "conflicts": conflicts,
        }

    def preflight(self, server: str, login: str) -> dict[str, Any]:
        plan = self._load_plan(server, login)
        target = self._target(plan.server)
        with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
            return self._preflight_live(api, plan)

    def preflight_server(self, server: str) -> dict[str, Any]:
        """Guard for future whole-router disaster recovery.

        Bulk restore is allowed only when the target has zero PPP secrets. This
        deliberately prefers a false block over duplicating a live router.
        """
        target = self._target(server)
        with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
            secrets = api.print("/ppp/secret")
            profiles = api.print("/ppp/profile")
            nat_rules = api.print("/ip/firewall/nat")
        return {
            "ok": len(secrets) == 0,
            "server": target.host,
            "live_secret_count": len(secrets),
            "live_profile_count": len(profiles),
            "live_nat_count": len(nat_rules),
            "reason": "" if not secrets else "Bulk restore blocked: target RouterOS already contains PPP secrets",
        }

    @staticmethod
    def _ret(replies: list[dict[str, str]]) -> str:
        for item in replies:
            value = str(item.get("ret") or "")
            if value:
                return value
        return ""

    def restore(self, server: str, login: str) -> dict[str, Any]:
        plan = self._load_plan(server, login)
        target = self._target(plan.server)
        created: list[tuple[str, str]] = []

        # Serialize against VPNSync snapshot work for this host. Direct Helper
        # writes are external, so a second fresh live preflight is still done
        # immediately before the first mutation.
        with self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                check = self._preflight_live(api, plan)
                if not check["ok"]:
                    raise ArchiveRestoreConflict("Restore blocked by live RouterOS conflicts", check["conflicts"])

                try:
                    if plan.profile and plan.profile not in {"default", "default-encryption"}:
                        profile_payload = self._clean(plan.profile_snapshot, self.PROFILE_WRITABLE)
                        profile_payload["name"] = plan.profile
                        if plan.local_address:
                            profile_payload["local-address"] = plan.local_address
                        if plan.remote_address:
                            profile_payload["remote-address"] = plan.remote_address
                        profile_payload.setdefault("use-upnp", "no")
                        profile_payload.setdefault("change-tcp-mss", "no")
                        profile_id = self._ret(api.talk("/ppp/profile/add", profile_payload))
                        if profile_id:
                            created.append(("/ppp/profile", profile_id))

                    secret_payload = self._clean(plan.secret_snapshot, self.SECRET_WRITABLE)
                    secret_payload.update({
                        "name": plan.login,
                        "password": plan.password,
                        "service": plan.service or "l2tp",
                        "profile": plan.profile or "default-encryption",
                        "disabled": "yes" if plan.disabled else "no",
                    })
                    if plan.comment:
                        secret_payload["comment"] = plan.comment
                    if plan.remote_address and not plan.profile:
                        secret_payload["remote-address"] = plan.remote_address
                    secret_id = self._ret(api.talk("/ppp/secret/add", secret_payload))
                    if secret_id:
                        created.append(("/ppp/secret", secret_id))

                    for item in plan.ports:
                        external = int(item.get("external_port") or 0)
                        if not 1 <= external <= 65535:
                            continue
                        # Recheck the exact external port immediately before add.
                        live = api.print("/ip/firewall/nat")
                        if any(external in self._parse_ports(rule.get("dst-port")) for rule in live):
                            raise ArchiveRestoreConflict(
                                f"Restore stopped: NAT port {external} became occupied",
                                [{"type": "nat-port", "value": external}],
                            )
                        internal = int(item.get("internal_port") or external)
                        payload = {
                            "chain": "dstnat",
                            "protocol": str(item.get("protocol") or "tcp").lower(),
                            "dst-port": str(external),
                            "action": "dst-nat",
                            "to-addresses": str(item.get("to_address") or plan.remote_address or ""),
                            "to-ports": str(internal),
                            "comment": plan.login,
                            "disabled": "yes" if bool(item.get("disabled")) else "no",
                        }
                        nat_id = self._ret(api.talk("/ip/firewall/nat/add", payload))
                        if nat_id:
                            created.append(("/ip/firewall/nat", nat_id))
                except Exception as exc:
                    rollback_errors: list[str] = []
                    for path, item_id in reversed(created):
                        try:
                            api.remove(path, item_id)
                        except Exception as rollback_exc:
                            rollback_errors.append(f"{path} {item_id}: {rollback_exc}")
                    if rollback_errors:
                        raise RuntimeError(
                            f"Restore failed and rollback was incomplete: {exc}; "
                            + "; ".join(rollback_errors)
                        ) from exc
                    raise

        # Listener normally catches the mutation. Run one coherent snapshot now
        # so PostgreSQL backup state is confirmed before reporting success.
        sync_result = self.monitor.syncer.sync(target, event_path="archive_restore")
        return {
            "ok": True,
            "server": plan.server,
            "login": plan.login,
            "backup_source": plan.source,
            "restored_objects": len(created),
            "sync": sync_result,
        }
