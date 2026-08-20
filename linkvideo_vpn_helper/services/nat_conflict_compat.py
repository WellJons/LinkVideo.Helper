from __future__ import annotations

"""Authoritative full-menu NAT conflict checks.

Exact RouterOS query words are treated as an optimization only.  Port ownership
and post-create safety are decided from a complete lightweight NAT inventory so
an empty ``?dst-port=`` response can never make a duplicate port look free.
"""

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.vpn_service import PortConflict, VPNService


_INSTALLED = False


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def install_nat_conflict_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_create = VPNService.create_clients_batch
    original_add_ports = VPNService.add_ports
    original_remove_port = VPNService.remove_port

    def robust_inspect_port_conflicts(self: VPNService, server, creds, client):
        ports = sorted({int(port) for port in (client.ports or []) if int(port) > 0})
        if not ports:
            return {}

        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            nat_proplist = ".id,chain,action,protocol,dst-port,to-addresses,to-ports,comment,disabled"
            try:
                nat_rows = api.print("/ip/firewall/nat", {".proplist": nat_proplist})
                if not nat_rows:
                    nat_rows = api.print("/ip/firewall/nat")
            except Exception:
                nat_rows = api.print("/ip/firewall/nat")
            try:
                secrets = api.print("/ppp/secret", {".proplist": ".id,name,remote-address,profile"})
                if not secrets:
                    secrets = api.print("/ppp/secret")
            except Exception:
                secrets = api.print("/ppp/secret")
            try:
                profiles = api.print("/ppp/profile", {".proplist": ".id,name,remote-address"})
                if not profiles:
                    profiles = api.print("/ppp/profile")
            except Exception:
                profiles = api.print("/ppp/profile")

        profile_remote = {
            str(row.get("name", "") or "").strip(): self._normalize_ip(row.get("remote-address", ""))
            for row in profiles or []
            if str(row.get("name", "") or "").strip()
        }
        remote_owners: dict[str, list[str]] = {}
        for secret in secrets or []:
            name = str(secret.get("name", "") or "").strip()
            if not name:
                continue
            remote = self._normalize_ip(secret.get("remote-address", ""))
            if not remote:
                remote = profile_remote.get(str(secret.get("profile", "") or "").strip(), "")
            if remote:
                remote_owners.setdefault(remote, []).append(name)

        own_ids = {str(value or "").strip() for value in (client.nat_rule_ids or []) if str(value or "").strip()}
        own_remote = self._normalize_ip(client.remote_address)
        conflicts: dict[int, list[PortConflict]] = {}
        seen: set[tuple[int, str, str, str]] = set()

        for row in nat_rows or []:
            rule_ports = self._parse_ports(self._get_rule_external_port(row))
            interested = [port for port in rule_ports if port in ports]
            if not interested:
                continue
            protocol = str(row.get("protocol", "tcp") or "tcp").strip().lower()
            if protocol and protocol not in {"tcp", "6", "6 (tcp)"}:
                continue
            chain = str(row.get("chain", "dstnat") or "dstnat").strip().lower()
            if chain and chain != "dstnat":
                continue
            rid = str(row.get(".id", "") or "").strip()
            comment = str(row.get("comment", "") or "").strip()
            remote = self._normalize_ip(self._get_rule_remote(row))

            # Same rule / same actual RouterOS destination is the client's own NAT.
            if rid and rid in own_ids:
                continue
            if own_remote and remote == own_remote:
                continue
            if comment == client.login and (not own_remote or not remote):
                continue

            owners = []
            if remote:
                owners.extend(remote_owners.get(remote, []))
            if comment and comment not in owners:
                owners.append(comment)
            owners = [owner for owner in _dedupe(owners) if owner != client.login]
            if not owners:
                owners = [""]

            for port in interested:
                for owner in owners:
                    key = (port, rid, remote, owner or comment)
                    if key in seen:
                        continue
                    seen.add(key)
                    conflicts.setdefault(port, []).append(PortConflict(
                        port=port,
                        rule_id=rid,
                        owner_login=owner,
                        owner_remote_address=remote,
                        owner_comment=comment,
                        disabled=self._is_rule_disabled(row),
                    ))
        return conflicts

    def safe_create(self: VPNService, server, creds, base_login, ports_per_client, accounts_count, progress_callback=None, cancel_event=None):
        created = original_create(
            self, server, creds, base_login, ports_per_client, accounts_count,
            progress_callback, cancel_event,
        )
        try:
            failures = []
            for client in created or []:
                conflicts = robust_inspect_port_conflicts(self, server, creds, client)
                if conflicts:
                    ports = ", ".join(str(port) for port in sorted(conflicts))
                    failures.append(f"{client.login}: конфликт портов {ports}")
                client.port_conflicts = conflicts
            if failures:
                raise RuntimeError("RouterOS подтвердил создание, но найдены дубли NAT: " + "; ".join(failures))
            return created
        except Exception as operation_error:
            rollback_errors: list[str] = []
            for item in reversed(created or []):
                try:
                    self._rollback_create(server, creds, item.profile_id, item.secret_id, list(item.nat_rule_ids or []))
                except Exception as rollback_error:
                    rollback_errors.append(f"{item.login}: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(
                    f"{operation_error}. Автоматический откат выполнен не полностью: "
                    + "; ".join(rollback_errors)
                ) from operation_error
            raise

    def safe_add_ports(self: VPNService, server, creds, login: str, count: int):
        before = self.get_client(server, creds, login)
        before_ports = set(int(port) for port in (before.ports if before else []))
        refreshed = original_add_ports(self, server, creds, login, count)
        new_ports = sorted(set(int(port) for port in refreshed.ports) - before_ports)
        try:
            conflicts = robust_inspect_port_conflicts(self, server, creds, refreshed)
            new_conflicts = {port: rows for port, rows in conflicts.items() if port in new_ports}
            if new_conflicts:
                raise RuntimeError(
                    "После добавления обнаружен конфликт NAT-портов: "
                    + ", ".join(str(port) for port in sorted(new_conflicts))
                )
            refreshed.port_conflicts = conflicts
            return refreshed
        except Exception as operation_error:
            # Internal rollback must work even for AdminChats, while the public
            # destructive UI action remains role-restricted.
            rollback_errors: list[str] = []
            for port in reversed(new_ports):
                try:
                    original_remove_port(self, server, creds, login, port)
                except Exception as rollback_error:
                    rollback_errors.append(f"порт {port}: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(
                    f"{operation_error}. Автоматический откат выполнен не полностью: "
                    + "; ".join(rollback_errors)
                ) from operation_error
            raise

    VPNService.inspect_port_conflicts = robust_inspect_port_conflicts
    VPNService.create_clients_batch = safe_create
    VPNService.add_ports = safe_add_ports
    _INSTALLED = True
